"""Per-host historical metrics sampler for Pulse-only hosts.

Sibling of :mod:`logic.host_metrics_sampler` (which serves the
node-exporter path) — same architectural shape, same skip-don't-
synthesize discipline (see the project conventions "Counter-rate samplers must
SKIP, not synthesize"), but sources its data from a single Pulse
hub probe per tick instead of per-host node-exporter scrapes.

Why a separate sampler from `host_metrics_sampler`:
  - Pulse data comes from ONE central API (`/api/state` on the
    Pulse hub) that returns every host's snapshot in one shot — a
    fundamentally different probe topology from per-host
    node-exporter scraping. Forking into a sibling module keeps
    each sampler simple instead of branching every probe step on
    "which provider".
  - Pulse-only hosts (Proxmox VMs / containers without a Beszel
    agent or node-exporter) have no other history surface — without
    this module the host-drawer chart grid stays empty AND the
    inline Hosts-row sparkline renders nothing. With it, the same
    host-drawer time-series envelope that
    `host_metrics_sampler.history_series` emits is available, so
    the SPA's chart helpers work unchanged across providers.

Schema mirrors `host_metrics_samples` but in its own table so a
Pulse-and-NE host (rare but possible — operator runs both) doesn't
write conflicting rows. Each table has one writer, one consumer.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from logic import pulse as _pulse
from logic import tuning
from logic.tuning import Tunable
from logic.db import (
    db_conn,
    get_setting,
    prune_rows_older_than,
    active_host_stats_providers as _active_providers,
    iter_curated_hosts,
)
from logic.settings_keys import Settings
# Numeric-coercion helpers — shared logic.coerce leaf module, aliased to
# the legacy underscore names so call sites are unchanged.
from logic.coerce import (
    safe_float as _safe_float,
    int_or_none as _int_or_none,
    float_or_none as _float_or_none,
)

# Same sanity bounds + rationale as host_metrics_sampler — see that
# module's docstring for the full discussion. Out-of-bounds deltas
# SKIP the row entirely; we never synthesize a 0 (would mask a
# reboot / counter wrap / clock skew the chart should expose).
_MIN_DELTA_SECONDS = 60
_MAX_DELTA_SECONDS = 900
_MIN_DELTA_BYTES = 0
_MAX_DELTA_BYTES = 10 * 1024 * 1024 * 1024  # 10 GB

# Per-host previous (ts, rx_bytes, tx_bytes) — module-level so the
# delta math survives across ticks. Cleared on lifespan
# cancel / restart so the post-restart first delta is correctly
# SKIPPED rather than stamping a synthesized zero.
_last_counters: dict[str, tuple[float, float, float]] = {}


def _curated_pulse_hosts() -> list[dict]:
    """Curated hosts opted-in for Pulse — one row per enabled host
    whose ``pulse_name`` field resolves a target.

    Mirrors ``host_metrics_sampler._load_curated_hosts`` shape. Lives
    locally because the row-shape is sampler-specific (we need just
    ``id`` and ``pulse_name``). The JSON-parse + enabled-gate prelude
    is delegated to :func:`logic.db.iter_curated_hosts`.
    """
    out: list[dict] = []
    for row in iter_curated_hosts():
        hid = (row.get("id") or "").strip()  # iter_curated_hosts already guarantees non-empty
        pname = (row.get("pulse_name") or "").strip()
        if not pname:
            continue
        out.append({"id": hid, "pulse_name": pname})
    return out


async def _probe_one_tick() -> dict:
    """Run ONE probe_pulse() and return ``{name: stats, ...}``.

    Single hub fetch covers every host — no per-host loop. Empty
    map on failure (probe_pulse never raises). Network errors land
    here as a logged warning so the sampler tick still completes.
    """
    base_url = (get_setting(Settings.PULSE_URL) or "").strip()
    token = (get_setting(Settings.PULSE_TOKEN) or "").strip()
    verify_tls = (get_setting(Settings.PULSE_VERIFY_TLS, "true") or "true").lower() == "true"
    if not base_url or not token:
        return {}
    timeout = float(tuning.tuning_int(Tunable.PULSE_PROBE_TIMEOUT_SECONDS)) or 15.0
    try:
        result = await _pulse.probe_pulse(
            base_url, token, verify_tls=verify_tls, timeout=timeout,
        )
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[host_pulse_sampler] probe_pulse failed: {e}")
        return {}
    if result.get("error"):
        print(f"[host_pulse_sampler] probe error: {result['error']}")
    hosts = result.get("hosts") or {}
    return hosts if isinstance(hosts, dict) else {}


# noinspection DuplicatedCode,PyTypeChecker
def _shape_row_for_db(host_id: str, stats: dict, now: float) -> Optional[tuple]:
    """Compute the persistable row for ONE host's tick.

    Net rates are computed against the previous tick's absolute
    counters; out-of-bounds deltas (reboot / wrap / clock skew /
    first-tick) SKIP the rate fields rather than store 0. Returns
    None when EVERY field is null / 0 / missing — the caller skips
    the INSERT entirely so empty rows don't poison the series.
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
        rx_now = _float_or_none(rx_total)
        tx_now = _float_or_none(tx_total)
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
    # Skip-empty: if every value is null / 0 / missing, don't insert.
    cpu_f = _safe_float(cpu)
    mem_total_f = _safe_float(mem_total)
    disk_total_f = _safe_float(disk_total)
    has_signal = (
        cpu_f > 0
        or mem_total_f > 0
        or disk_total_f > 0
        or (nr_bps is not None) or (ns_bps is not None)
    )
    if not has_signal:
        # Diagnostic — operator chasing "wrote=0 even though looked_up>0"
        # needs to see WHICH field came back null for which host. The
        # tick summary's `wrote=0` only tells us the gate fired; this
        # per-host trace tells us why. Throttled at the sampler tick
        # cadence (~5 min) so safe to print verbatim. Lists the first
        # 12 stats keys + first 3 sample values so we can tell whether
        # extract_guest_stats produced the host_* prefix at all.
        sample_keys = sorted(stats.keys())[:12] if isinstance(stats, dict) else []
        print(
            f"[host_pulse_sampler] skip-empty {host_id}: "
            f"cpu={cpu!r} mem_total={mem_total!r} mem_used={mem_used!r} "
            f"disk_total={disk_total!r} disk_used={disk_used!r} "
            f"rx={rx_total!r} tx={tx_total!r} "
            f"stats_keys={sample_keys}"
        )
        return None
    return (
        int(now), host_id,
        _float_or_none(cpu),
        _int_or_none(mem_total),
        _int_or_none(mem_used),
        _int_or_none(disk_total),
        _int_or_none(disk_used),
        nr_bps, ns_bps,
    )


async def _persist_tick(rows: list[tuple]) -> None:
    if not rows:
        return
    try:
        with db_conn() as c:
            c.executemany(
                "INSERT INTO host_pulse_samples "
                "(ts, host_id, cpu_percent, mem_total, mem_used, "
                " disk_total, disk_used, net_rx_bps, net_tx_bps) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[host_pulse_sampler] persist failed: {e}")


def _prune_old_rows_sync() -> int:
    """Synchronous body — DELETE rows older than ``STATS_HISTORY_DAYS``
    (default 7). Called from the async wrapper below via
    `asyncio.to_thread` so the sync SQLite DELETE doesn't stall the
    event loop on large fleets where the prune can take seconds.

    MUST return the deleted-row count as an int so the
    `prune_with_metrics` wrapper records non-zero `last_prune_rows`
    in the Stats → Samplers dashboard (pre-fix returned None which
    the helper silently coerced to 0).
    """
    days = max(1, int(tuning.tuning_int(Tunable.STATS_HISTORY_DAYS)) or 7)
    cutoff = int(time.time() - days * 86400)
    removed = 0
    try:
        # Chunked delete (writer lock released per chunk) instead of one big
        # DELETE — same predicate, bounded lock-hold, seeks idx_host_pulse_samples_ts.
        removed = prune_rows_older_than("host_pulse_samples", cutoff)
    except Exception as e:  # noqa: BLE001
        print(f"[host_pulse_sampler] prune failed: {e}")
    return removed


async def _prune_old_rows() -> None:
    """Async wrapper — offloads the sync prune to a worker thread so
    the event loop stays responsive for /api/* + SSE during the
    hourly DELETE. Same pattern as
    `host_metrics_sampler._prune_old_samples`. Routed through
    `prune_with_metrics` so the Stats → Samplers panel records the
    prune's row count + wall-clock duration."""
    from logic.sampler_metrics import prune_with_metrics
    await prune_with_metrics("host_pulse_sampler", _prune_old_rows_sync)


# noinspection DuplicatedCode,PyTypeChecker
async def host_pulse_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every
    ``tuning_stats_sample_interval_seconds``; dormant when ``pulse``
    isn't an active host-stats provider OR no curated host has
    ``pulse_name`` set.
    """
    print("[host_pulse_sampler] lifespan started")
    last_prune = 0.0
    iter_count = 0
    from logic.sampler_metrics import record_tick as _record_tick
    try:
        while True:
            # Pulse-specific interval > 0 overrides the global stats
            # interval; 0 = inherit (legacy / parity with Beszel knob).
            pulse_interval = int(tuning.tuning_int(Tunable.PULSE_SAMPLE_INTERVAL_SECONDS))
            interval = (pulse_interval if pulse_interval > 0
                        else int(tuning.tuning_int(Tunable.STATS_SAMPLE_INTERVAL_SECONDS))) or 300
            interval = max(30, interval)
            iter_count += 1
            # Unconditional per-iteration log — fires BEFORE the
            # active / curated gates so silent-sleep paths are
            # visible. Without this, a sampler that's silently
            # bailing on the gates produces ZERO log output (the
            # `tick:` summary only fires AFTER a successful probe),
            # leaving operators unable to distinguish "sampler is
            # alive but gated" from "sampler isn't running".
            active_set = _active_providers()
            print(
                f"[host_pulse_sampler] iter {iter_count}: "
                f"active={sorted(active_set)} interval={interval}s"
            )
            _tick_t0 = time.perf_counter()
            _tick_ok = True
            _tick_err = ""
            try:
                if "pulse" not in active_set:
                    print(f"[host_pulse_sampler] iter {iter_count} skip: pulse not in active")
                    await asyncio.sleep(interval)
                    continue
                hosts = _curated_pulse_hosts()
                if not hosts:
                    print(f"[host_pulse_sampler] iter {iter_count} skip: no curated pulse hosts")
                    await asyncio.sleep(interval)
                    continue
                hub_map = await _probe_one_tick()
                now = time.time()
                # Visibility log — operators chasing "why are my
                # host_pulse_samples count=0?" need to see whether
                # the sampler IS ticking AND where the lookup falls
                # off. Same shape as the host_beszel_sampler logging.
                lookup_hits = 0
                rows: list[tuple] = []
                for h in hosts:
                    hid = h["id"]
                    pname = h["pulse_name"]
                    stats = _pulse.lookup(hub_map, pname) if hub_map else None
                    if not isinstance(stats, dict):
                        continue
                    lookup_hits += 1
                    row = _shape_row_for_db(hid, stats, now)
                    if row is not None:
                        rows.append(row)
                if rows:
                    await _persist_tick(rows)
                # One-line tick summary — same shape as the
                # host_beszel_sampler. Lets operators confirm the
                # sampler is alive AND see where hosts fall on the
                # lookup-vs-shape gate without instrumenting per-host.
                print(
                    f"[host_pulse_sampler] tick: curated={len(hosts)} "
                    f"hub_keys={len(hub_map or {})} "
                    f"looked_up={lookup_hits} wrote={len(rows)} "
                    f"interval={interval}s"
                )
                # Hourly retention prune.
                if (now - last_prune) > 3600:
                    await _prune_old_rows()
                    last_prune = now
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as e:  # noqa: BLE001
                _tick_ok = False
                _tick_err = type(e).__name__
                print(f"[host_pulse_sampler] tick error: {e}")
            finally:
                _record_tick(
                    "host_pulse_sampler",
                    (time.perf_counter() - _tick_t0) * 1000.0,
                    ok=_tick_ok,
                    error=_tick_err,
                )
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        print("[host_pulse_sampler] lifespan cancelled")
        raise


# ---- Read helpers (consumed by /api/hosts/history dispatch) -----

# noinspection DuplicatedCode,PyTypeChecker
def recent_samples(host_id: str, since_ts: int, limit: int = 500) -> list[dict]:
    """Return rows for one host back to ``since_ts`` (epoch s),
    oldest-first. Empty list when the host has no rows yet.
    """
    if not host_id:
        return []
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, cpu_percent, mem_total, mem_used, "
                "disk_total, disk_used, net_rx_bps, net_tx_bps "
                "FROM host_pulse_samples WHERE host_id=? AND ts >= ? "
                "ORDER BY ts ASC LIMIT ?",
                (host_id, int(since_ts), int(limit)),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[host_pulse_sampler] recent_samples({host_id!r}) failed: {e}")
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
    """Return the host-drawer time-series envelope for one Pulse-only host.

    Mirrors ``host_metrics_sampler.history_series``'s output shape
    field-for-field — same key names (``cpu`` / ``mp`` / ``dp`` / ``mu``
    / ``du`` / ``b`` / ``nr`` / ``ns`` / ``net`` / ``dr`` / ``dw`` /
    ``la1`` / ``la5`` / ``la15`` / ``s`` / ``su``) so the SPA's chart
    helpers consume this path without branching on provider. Pulse
    doesn't expose load avg / swap / per-disk I/O so those return 0;
    the SPA's per-card ``maxRaw > 0`` gates hide those panels cleanly.
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
            # Pulse doesn't expose per-disk I/O / load avg / swap.
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
    """Newest-first recent rows for the debug endpoint. Mirrors
    ``host_metrics_sampler.last_samples``'s contract.
    """
    if not host_id:
        return []
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, cpu_percent, mem_total, mem_used, "
                "disk_total, disk_used, net_rx_bps, net_tx_bps "
                "FROM host_pulse_samples WHERE host_id=? "
                "ORDER BY ts DESC LIMIT ?",
                (host_id, int(limit)),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[host_pulse_sampler] last_samples({host_id!r}) failed: {e}")
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
