"""Per-host historical metrics sampler for Webmin-only hosts.

Sibling of :mod:`logic.host_metrics_sampler` (NE) and
:mod:`logic.host_pulse_sampler` (Pulse) — same architectural shape,
same skip-don't-synthesize discipline (CLAUDE.md "Counter-rate
samplers must SKIP, not synthesize"), but sources its data from
per-host Miniserv probes.

Why a separate sampler:
  - Webmin is per-host (one Miniserv per target box, like NE) — NOT
    a central hub like Beszel / Pulse. Probing is fan-out across
    curated rows with `webmin_url` set.
  - Webmin's `extract_stats` shape overlaps NE significantly
    (cpu_percent / mem_total / mem_used / disk_total / disk_used /
    mounts / interfaces). Pure-Webmin hosts (rare — Miniserv is
    usually paired with another provider) get inline Hosts-row
    sparklines + drawer charts via this sampler.
  - Forking from `host_metrics_sampler` keeps each sampler simple
    instead of branching every probe step on "which provider".

Schema mirrors `host_metrics_samples` / `host_pulse_samples` so the
SPA's chart helpers + inline sparkline data-source ladder treat
Webmin-only hosts identically to NE / Pulse hosts.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from logic import webmin as _webmin
from logic import tuning
from logic.tuning import Tunable
from logic.db import (
    db_conn,
    get_setting,
    active_host_stats_providers as _active_providers,
    iter_curated_hosts,
)
from logic.settings_keys import Settings

# Same sanity bounds as host_metrics_sampler / host_pulse_sampler.
_MIN_DELTA_SECONDS = 60
_MAX_DELTA_SECONDS = 900
_MIN_DELTA_BYTES = 0
_MAX_DELTA_BYTES = 10 * 1024 * 1024 * 1024  # 10 GB

# Per-host previous (ts, rx_bytes, tx_bytes) — module-level so the
# delta math survives across ticks. Cleared on lifespan
# cancel / restart so the post-restart first delta is correctly
# SKIPPED.
_last_counters: dict[str, tuple[float, float, float]] = {}


def _curated_webmin_hosts() -> list[dict]:
    """Curated hosts opted-in for Webmin sampling.

    Walks `hosts_config` for rows whose `webmin_url` (resolved via the
    `webmin_aliases` settings map) is set. Returns one row per enabled
    entry as `{id, url}`. Empty list when Webmin isn't a registered
    provider on the curated row. The JSON-parse + enabled-gate prelude
    is delegated to :func:`logic.db.iter_curated_hosts`.
    """
    try:
        aliases = json.loads(get_setting(Settings.WEBMIN_ALIASES, "{}") or "{}")
        if not isinstance(aliases, dict):
            aliases = {}
    except ValueError:
        aliases = {}
    out: list[dict] = []
    for row in iter_curated_hosts():
        hid = (row.get("id") or "").strip()  # iter_curated_hosts already guarantees non-empty
        # Resolution chain: webmin_aliases[id] → row.webmin_url →
        # row.webmin_name (last-resort, matches the curated alias
        # convention some operators use). Empty all three = skip.
        url = (
            (aliases.get(hid) or "").strip().rstrip("/")
            or (row.get("webmin_url") or "").strip().rstrip("/")
        )
        if not url:
            continue
        out.append({"id": hid, "url": url})
    return out


# noinspection DuplicatedCode,PyTypeChecker
def _shape_row_for_db(host_id: str, stats: dict, now: float) -> Optional[tuple]:
    """Compute the persistable row for ONE host's tick.

    Same skip-don't-synthesize rules as the sibling samplers — net
    deltas out of bounds SKIP the rate fields rather than synthesize
    0. Returns None when every signal field is null / 0.
    """
    cpu = stats.get("host_cpu_percent")
    mem_total = stats.get("host_mem_total")
    mem_used = stats.get("host_mem_used")
    disk_total = stats.get("host_disk_total")
    disk_used = stats.get("host_disk_used")
    rx_total = stats.get("host_net_rx_total_bytes")
    tx_total = stats.get("host_net_tx_total_bytes")
    nr_bps: Optional[float] = None
    ns_bps: Optional[float] = None
    if rx_total is not None and tx_total is not None:
        prev = _last_counters.get(host_id)
        try:
            rx_now = float(rx_total)
            tx_now = float(tx_total)
        except (TypeError, ValueError):
            rx_now = tx_now = None  # type: ignore[assignment]
        if rx_now is not None and tx_now is not None:
            if prev is not None:
                prev_ts, prev_rx, prev_tx = prev
                ds = now - prev_ts
                drx = rx_now - prev_rx
                dtx = tx_now - prev_tx
                if (_MIN_DELTA_SECONDS <= ds <= _MAX_DELTA_SECONDS
                    and _MIN_DELTA_BYTES <= drx <= _MAX_DELTA_BYTES
                    and _MIN_DELTA_BYTES <= dtx <= _MAX_DELTA_BYTES):
                    nr_bps = drx / ds
                    ns_bps = dtx / ds
            _last_counters[host_id] = (now, rx_now, tx_now)
    has_signal = (
        (cpu is not None and float(cpu) > 0)
        or (mem_total is not None and float(mem_total) > 0)
        or (disk_total is not None and float(disk_total) > 0)
        or (nr_bps is not None) or (ns_bps is not None)
    )
    if not has_signal:
        return None
    return (
        int(now), host_id,
        float(cpu) if cpu is not None else None,
        int(mem_total) if mem_total is not None else None,
        int(mem_used) if mem_used is not None else None,
        int(disk_total) if disk_total is not None else None,
        int(disk_used) if disk_used is not None else None,
        nr_bps, ns_bps,
    )


async def _probe_one_host(client_url: str, user: str, password: str,
                          verify_tls: bool, timeout: float,
                          active: set[str]) -> Optional[dict]:
    """Probe one Webmin host, return its merged host_* stats dict
    or None when the probe failed / returned empty.
    """
    try:
        result = await _webmin.probe_webmin(
            client_url, user, password,
            verify_tls=verify_tls, timeout=timeout,
            active_sources=active,
        )
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[host_webmin_sampler] probe failed for {client_url!r}: {e}")
        return None
    hosts = result.get("hosts") or {}
    if not isinstance(hosts, dict) or not hosts:
        return None
    # Webmin returns ONE host per call (per-host topology) — take
    # the first / only entry.
    return next(iter(hosts.values()))


async def _persist_tick(rows: list[tuple]) -> None:
    if not rows:
        return
    try:
        with db_conn() as c:
            c.executemany(
                "INSERT INTO host_webmin_samples "
                "(ts, host_id, cpu_percent, mem_total, mem_used, "
                " disk_total, disk_used, net_rx_bps, net_tx_bps) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[host_webmin_sampler] persist failed: {e}")


async def _prune_old_rows() -> None:
    days = max(1, int(tuning.tuning_int(Tunable.STATS_HISTORY_DAYS)) or 7)
    cutoff = int(time.time() - days * 86400)
    try:
        with db_conn() as c:
            c.execute(
                "DELETE FROM host_webmin_samples WHERE ts < ?", (cutoff,),
            )
    except Exception as e:  # noqa: BLE001
        print(f"[host_webmin_sampler] prune failed: {e}")


async def host_webmin_sampler_loop() -> None:
    """Lifespan-managed Webmin sampler. Dormant when ``webmin`` isn't
    an active provider OR no curated host has a `webmin_url` set."""
    print("[host_webmin_sampler] lifespan started")
    last_prune = 0.0
    iter_count = 0
    try:
        while True:
            interval = max(30, int(tuning.tuning_int(Tunable.STATS_SAMPLE_INTERVAL_SECONDS)) or 300)
            iter_count += 1
            active_set = _active_providers()
            print(
                f"[host_webmin_sampler] iter {iter_count}: "
                f"active={sorted(active_set)} interval={interval}s"
            )
            try:
                if "webmin" not in active_set:
                    print(f"[host_webmin_sampler] iter {iter_count} skip: webmin not in active")
                    await asyncio.sleep(interval)
                    continue
                hosts = _curated_webmin_hosts()
                if not hosts:
                    print(f"[host_webmin_sampler] iter {iter_count} skip: no curated webmin hosts")
                    await asyncio.sleep(interval)
                    continue
                user = (get_setting(Settings.WEBMIN_USER) or "").strip()
                password = (get_setting(Settings.WEBMIN_PASSWORD) or "").strip()
                if not user or not password:
                    print(f"[host_webmin_sampler] iter {iter_count} skip: missing creds")
                    await asyncio.sleep(interval)
                    continue
                verify_tls = (get_setting(Settings.WEBMIN_VERIFY_TLS, "false") or "false").lower() == "true"
                timeout = float(tuning.tuning_int(Tunable.WEBMIN_PROBE_TIMEOUT_SECONDS)) or 8.0
                active = _active_providers()
                # Fan out — one probe per curated host, in parallel
                # via gather. Webmin probes are independent so this
                # is safe; per-host concurrency matches NE's pattern.
                tasks = [
                    _probe_one_host(h["url"], user, password,
                                    verify_tls, timeout, active)
                    for h in hosts
                ]
                # Outer wall-clock budget so a wedged Webmin host can't
                # starve the whole sampler tick. Operator-tunable via
                # `tuning_webmin_sampler_budget_seconds`; the default 0
                # ("auto") derives from probe_timeout × N hosts capped
                # at 5 min. Explicit non-zero values pin the budget
                # for fleets with unusual fan-out shapes.
                _budget_override = tuning.tuning_int(Tunable.WEBMIN_SAMPLER_BUDGET_SECONDS)
                if _budget_override > 0:
                    _outer_budget = float(_budget_override)
                else:
                    _outer_budget = min(300.0, max(15.0, timeout * max(1, len(tasks))))
                try:
                    results = await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True),
                        timeout=_outer_budget,
                    )
                except asyncio.TimeoutError:
                    print(
                        f"[host_webmin_sampler] tick wall-clock budget "
                        f"({_outer_budget:.0f}s) exceeded across {len(tasks)} "
                        f"hosts — dropping partial results, will retry next tick"
                    )
                    results = []
                now = time.time()
                rows: list[tuple] = []
                probed_ok = 0
                for h, res in zip(hosts, results):
                    if isinstance(res, BaseException) or not isinstance(res, dict):
                        continue
                    probed_ok += 1
                    row = _shape_row_for_db(h["id"], res, now)
                    if row is not None:
                        rows.append(row)
                if rows:
                    await _persist_tick(rows)
                # Per-tick visibility — same shape as the Pulse +
                # Beszel samplers. Webmin is per-host (not hub-batch)
                # so the columns describe `probed=K` (probes that
                # returned a usable dict) instead of `looked_up=K`
                # (lookups that hit the hub map). `wrote=L` after the
                # `_shape_row_for_db` skip-empty gate. `interval` is
                # the resolved cadence in seconds.
                print(
                    f"[host_webmin_sampler] tick: curated={len(hosts)} "
                    f"probed={probed_ok} wrote={len(rows)} "
                    f"interval={interval}s"
                )
                if (now - last_prune) > 3600:
                    await _prune_old_rows()
                    last_prune = now
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as e:  # noqa: BLE001
                print(f"[host_webmin_sampler] tick error: {e}")
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        print("[host_webmin_sampler] lifespan cancelled")
        raise


# ---- Read helpers ----

# noinspection DuplicatedCode,PyTypeChecker
def recent_samples(host_id: str, since_ts: int, limit: int = 500) -> list[dict]:
    """Return up to `limit` host_webmin_samples rows for `host_id` newer than `since_ts`."""
    if not host_id:
        return []
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, cpu_percent, mem_total, mem_used, "
                "disk_total, disk_used, net_rx_bps, net_tx_bps "
                "FROM host_webmin_samples WHERE host_id=? AND ts >= ? "
                "ORDER BY ts ASC LIMIT ?",
                (host_id, int(since_ts), int(limit)),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[host_webmin_sampler] recent_samples({host_id!r}) failed: {e}")
        return []
    out: list[dict] = []
    for r in rows:
        out.append({
            "ts": int(r[0]),
            "cpu_percent": (float(r[1]) if r[1] is not None else None),
            "mem_total": (int(r[2]) if r[2] is not None else None),
            "mem_used": (int(r[3]) if r[3] is not None else None),
            "disk_total": (int(r[4]) if r[4] is not None else None),
            "disk_used": (int(r[5]) if r[5] is not None else None),
            "net_rx_bps": (float(r[6]) if r[6] is not None else None),
            "net_tx_bps": (float(r[7]) if r[7] is not None else None),
        })
    return out


# noinspection DuplicatedCode,PyTypeChecker
def history_series(host_id: str, hours: int) -> list[dict]:
    """Beszel-compatible series envelope. Field-for-field match with
    `host_pulse_sampler.history_series` / `host_metrics_sampler.history_series`
    so the SPA's chart helpers work against this path with no branching.
    """
    hours = max(1, min(168, int(hours or 1)))
    since = int(time.time() - hours * 3600)
    raw = recent_samples(host_id, since, limit=hours * 60)
    gib = 1024 ** 3
    series: list[dict] = []
    for r in raw:
        mem_total = r.get("mem_total") or 0
        mem_used = r.get("mem_used") or 0
        disk_total = r.get("disk_total") or 0
        disk_used = r.get("disk_used") or 0
        nr = r.get("net_rx_bps") or 0.0
        ns = r.get("net_tx_bps") or 0.0
        net = nr + ns
        series.append({
            "t": r["ts"],
            "cpu": r.get("cpu_percent") or 0.0,
            "mp": (100.0 * mem_used / mem_total) if mem_total else 0.0,
            "dp": (100.0 * disk_used / disk_total) if disk_total else 0.0,
            "mu": (mem_used / gib) if mem_used else 0.0,
            "du": (disk_used / gib) if disk_used else 0.0,
            "b": net,
            "nr": nr,
            "ns": ns,
            "net": net,
            # Webmin doesn't expose per-disk I/O / load avg / swap.
            "dr": 0.0,
            "dw": 0.0,
            "la1": 0.0,
            "la5": 0.0,
            "la15": 0.0,
            "s": 0.0,
            "su": 0.0,
        })
    return series


# noinspection DuplicatedCode,PyTypeChecker
def last_samples(host_id: str, limit: int = 5) -> list[dict]:
    """Return the most recent `limit` host_webmin_samples rows for `host_id`."""
    if not host_id:
        return []
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, cpu_percent, mem_total, mem_used, "
                "disk_total, disk_used, net_rx_bps, net_tx_bps "
                "FROM host_webmin_samples WHERE host_id=? "
                "ORDER BY ts DESC LIMIT ?",
                (host_id, int(limit)),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[host_webmin_sampler] last_samples({host_id!r}) failed: {e}")
        return []
    out: list[dict] = []
    for r in rows:
        out.append({
            "ts": int(r[0]),
            "cpu_percent": (float(r[1]) if r[1] is not None else None),
            "mem_total": (int(r[2]) if r[2] is not None else None),
            "mem_used": (int(r[3]) if r[3] is not None else None),
            "disk_total": (int(r[4]) if r[4] is not None else None),
            "disk_used": (int(r[5]) if r[5] is not None else None),
            "net_rx_bps": (float(r[6]) if r[6] is not None else None),
            "net_tx_bps": (float(r[7]) if r[7] is not None else None),
        })
    return out
