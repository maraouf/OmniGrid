"""Per-host historical metrics sampler for Beszel-tracked hosts.

Sibling of :mod:`logic.host_pulse_sampler` and the SNMP block inside
:mod:`logic.host_metrics_sampler` — same architectural shape, same
skip-don't-synthesize discipline. Sources its data from a single
Beszel hub probe per tick instead of per-host scrapes.

Why a separate sampler from `host_metrics_sampler`:
  - Beszel data comes from ONE central API (PocketBase ``systems`` +
    ``system_stats`` collections) that returns every host's snapshot
    in one shot — same probe topology as Pulse.
  - Pre-fix Beszel was the read-through-only outlier in the provider
    fleet — every chart query hit the hub directly, with no local
    cache. When the hub's ``1m`` aggregation tier aged out (~1h
    retention), the data was gone and OmniGrid had no fallback. This
    sampler closes that gap by writing a row per host per tick into
    ``host_beszel_samples``, putting Beszel data inside OmniGrid's
    own retention window (``tuning_stats_history_days``, default 7d).
  - The "every host-stats provider must have a local sample store"
    rule (recorded in the project conventions / agent memory) treats read-through-
    only as forbidden because two failure modes can't be hidden
    otherwise: (a) upstream retention shorter than ours; (b)
    upstream cadence different from ours.

Schema mirrors ``host_pulse_samples`` field-for-field so a Beszel +
Pulse host doesn't cross-contaminate, and the ``history_series``
helper below emits the same Beszel-compatible series envelope every
other sampler module produces — the SPA's chart helpers work against
this path with no branching.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from logic import beszel as _beszel
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

# Same sanity bounds as the Pulse sampler — see that module's
# docstring for the full discussion. Out-of-bounds deltas SKIP the
# row entirely; we never synthesize a 0 (would mask a reboot /
# counter wrap / clock skew the chart should expose).
_MIN_DELTA_SECONDS = 60
_MAX_DELTA_SECONDS = 900
_MIN_DELTA_BYTES = 0
_MAX_DELTA_BYTES = 10 * 1024 * 1024 * 1024  # 10 GB

# Per-host previous (ts, rx_bytes, tx_bytes) — module-level so the
# delta math survives across ticks. Cleared on lifespan
# cancel / restart so the post-restart first delta is correctly
# SKIPPED rather than stamping a synthesized zero.
_last_counters: dict[str, tuple[float, float, float]] = {}


def _curated_beszel_hosts() -> list[dict]:
    """Curated hosts opted-in for Beszel — one row per enabled host
    whose ``beszel_name`` field resolves a target.

    Mirrors ``logic.host_pulse_sampler._curated_pulse_hosts``. Lives
    locally because the row-shape is sampler-specific (we need just
    ``id`` and ``beszel_name``). The JSON-parse + enabled-gate prelude
    is delegated to :func:`logic.db.iter_curated_hosts`.
    """
    out: list[dict] = []
    for row in iter_curated_hosts():
        hid = (row.get("id") or "").strip()  # iter_curated_hosts already guarantees non-empty
        bname = (row.get("beszel_name") or "").strip()
        if not bname:
            continue
        out.append({"id": hid, "beszel_name": bname})
    return out


async def _probe_one_tick() -> dict:
    """Run ONE probe_hub() and return ``{name: stats, ...}``.

    Single hub fetch covers every host — no per-host loop. Empty map
    on failure (probe_hub never raises). Network errors land here as
    a logged warning so the sampler tick still completes.
    """
    base_url = (get_setting(Settings.BESZEL_HUB_URL) or "").strip()
    ident = (get_setting(Settings.BESZEL_IDENTITY) or "").strip()
    passw = (get_setting(Settings.BESZEL_PASSWORD) or "").strip()
    verify_tls = (get_setting(Settings.BESZEL_VERIFY_TLS, "true") or "true").lower() == "true"
    if not base_url or not ident or not passw:
        return {}
    # Operator-tunable hub-probe timeout. Falls back to the
    # `probe_hub` default (15s) when the tunable resolves to 0 / a
    # bad value. Range-clamped via `tuning_int`'s built-in (lo, hi)
    # enforcement.
    timeout = float(tuning.tuning_int(Tunable.BESZEL_PROBE_TIMEOUT_SECONDS)) or 15.0
    try:
        result = await _beszel.probe_hub(
            base_url, ident, passw,
            verify_tls=verify_tls, timeout=timeout,
        )
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[host_beszel_sampler] probe_hub failed: {e}")
        return {}
    if result.get("error"):
        print(f"[host_beszel_sampler] probe error: {result['error']}")
    systems = result.get("systems") or {}
    return systems if isinstance(systems, dict) else {}


# noinspection DuplicatedCode,PyTypeChecker
def _shape_row_for_db(host_id: str, stats: dict, now: float) -> Optional[tuple]:
    """Compute the persistable row for ONE host's tick.

    Net rates are computed against the previous tick's absolute
    counters; out-of-bounds deltas (reboot / wrap / clock skew /
    first-tick) SKIP the rate fields rather than store 0. Returns
    None when EVERY field is null / 0 / missing — the caller skips
    the INSERT entirely so empty rows don't poison the series.

    Beszel's ``probe_hub`` extracts stats via ``extract_stats`` which
    normalises the per-host schema into the same ``host_*`` keys
    every other provider uses, so the field names below match Pulse
    field-for-field.
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
    # Chart-extras: load avg + swap + temps + GPUs + bandwidth +
    # container count. Captured per-tick so the drawer's Load / Swap
    # / Temperature / GPU chart cards survive a hub-side outage. Same
    # skip-don't-synthesize discipline applies — the field is stored
    # as None when the agent doesn't emit it; only the basic-signal
    # check above gates whether the row is INSERTed at all.
    load_1m = stats.get("host_load_1m")
    load_5m = stats.get("host_load_5m")
    load_15m = stats.get("host_load_15m")
    swap_pct = stats.get("host_swap_percent")
    swap_used = stats.get("host_swap_used")
    bandwidth = stats.get("host_bandwidth")
    containers = stats.get("host_containers")
    temperatures = stats.get("host_temperatures")
    gpus = stats.get("host_gpus")
    has_signal = (
        (cpu is not None and float(cpu) > 0)
        or (mem_total is not None and float(mem_total) > 0)
        or (disk_total is not None and float(disk_total) > 0)
        or (nr_bps is not None) or (ns_bps is not None)
    )
    if not has_signal:
        return None
    import json as _json
    temps_json = None
    if temperatures and isinstance(temperatures, (dict, list)):
        try:
            temps_json = _json.dumps(temperatures, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            temps_json = None
    gpus_json = None
    if gpus and isinstance(gpus, list):
        try:
            gpus_json = _json.dumps(gpus, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            gpus_json = None
    return (
        int(now), host_id,
        float(cpu) if cpu is not None else None,
        int(mem_total) if mem_total is not None else None,
        int(mem_used) if mem_used is not None else None,
        int(disk_total) if disk_total is not None else None,
        int(disk_used) if disk_used is not None else None,
        nr_bps, ns_bps,
        float(load_1m) if load_1m is not None else None,
        float(load_5m) if load_5m is not None else None,
        float(load_15m) if load_15m is not None else None,
        float(swap_pct) if swap_pct is not None else None,
        float(swap_used) if swap_used is not None else None,
        float(bandwidth) if bandwidth is not None else None,
        int(containers) if containers is not None else None,
        temps_json, gpus_json,
    )


async def _persist_tick(rows: list[tuple]) -> None:
    """Bulk-insert this tick's Beszel sample rows (no-op on an empty batch)."""
    if not rows:
        return
    try:
        with db_conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO host_beszel_samples "
                "(ts, host_id, cpu_percent, mem_total, mem_used, "
                " disk_total, disk_used, net_rx_bps, net_tx_bps, "
                " load_1m, load_5m, load_15m, "
                " swap_percent, swap_used, bandwidth, containers, "
                " temperatures_json, gpus_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[host_beszel_sampler] persist failed: {e}")


async def _persist_services(host_id: str, services_raw: list, now: float) -> None:
    """UPSERT per-unit rows in `host_beszel_services` for one host.

    `services_raw` is the raw `systemd_services` PocketBase list
    that `probe_hub` attaches to each system's stats dict as
    ``host_services_raw``. Each record has ``{name, state, sub, ...}``
    where ``state`` is the systemd ActiveState enum (0=active,
    1=reloading, 2=inactive, 3=failed, 4=activating, 5=deactivating).

    On every tick we:
      - For each unit on this host, UPSERT the (host_id, name) row.
      - `last_seen_ts` always advances to the current tick.
      - `last_change_ts` advances ONLY when the state changed since
        the previous snapshot — preserves the "failed since 2h ago"
        affordance even though we don't keep a transition log.

    Units that have DISAPPEARED from the agent's view (stale rows
    no longer in `services_raw`) are NOT deleted here — the SPA
    gates on `last_seen_ts` to fade them. A retention sweep prunes
    rows that haven't been seen in `tuning_stats_history_days` days.
    """
    if not host_id or not isinstance(services_raw, list) or not services_raw:
        return
    try:
        with db_conn() as c:
            # Pre-fetch existing state for this host so we know which
            # units changed state vs stayed the same (drives
            # `last_change_ts`). Sized for typical hosts (10–100
            # units); not paginated — runaway-services hosts would
            # need a different storage strategy anyway.
            existing = {
                row[0]: (row[1], row[2])
                for row in c.execute(
                    "SELECT service_name, state, last_change_ts "
                    "FROM host_beszel_services WHERE host_id = ?",
                    (host_id,),
                ).fetchall()
            }
            upserts: list[tuple] = []
            for s in services_raw:
                if not isinstance(s, dict):
                    continue
                name = str(s.get("name") or s.get("n") or "").strip()
                if not name:
                    continue
                state_raw = s.get("state")
                state = (int(state_raw)
                         if isinstance(state_raw, (int, float)) else None)
                sub_raw = s.get("sub")
                sub_state = (int(sub_raw)
                             if isinstance(sub_raw, (int, float)) else None)
                prior = existing.get(name)
                if prior and prior[0] == state:
                    last_change_ts = prior[1]
                else:
                    last_change_ts = int(now)
                upserts.append((
                    host_id, name, state, sub_state,
                    int(now), int(last_change_ts),
                ))
            if upserts:
                c.executemany(
                    "INSERT OR REPLACE INTO host_beszel_services "
                    "(host_id, service_name, state, sub_state, "
                    " last_seen_ts, last_change_ts) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    upserts,
                )
    except Exception as e:  # noqa: BLE001
        print(f"[host_beszel_sampler] services persist failed: {e}")


def _prune_old_rows_sync() -> int:
    """Synchronous prune body. Sweeps both the time-series
    `host_beszel_samples` table AND the per-unit `host_beszel_services`
    table. Service rows whose `last_seen_ts` predates the retention
    cutoff are deleted (the Beszel agent stopped reporting them —
    user removed the unit, moved the host, etc.). Called from
    the async wrapper below via `asyncio.to_thread` so the sync SQLite
    DELETEs don't stall the event loop.

    Returns the SUM of deleted rows across BOTH DELETEs as a single
    int. The `prune_with_metrics` wrapper expects this so Stats →
    Samplers' `last_prune_rows` column reflects the real prune work
    (pre-fix returned None → silently coerced to 0 by the helper).
    """
    days = max(1, int(tuning.tuning_int(Tunable.STATS_HISTORY_DAYS)) or 7)
    cutoff = int(time.time() - days * 86400)
    removed = 0
    try:
        # Chunked deletes (writer lock released per chunk) instead of one big
        # DELETE per table — same predicate, bounded lock-hold. The services
        # sweep keys on last_seen_ts (seeks idx_host_beszel_services_last_seen_ts).
        removed += prune_rows_older_than("host_beszel_samples", cutoff)
        removed += prune_rows_older_than("host_beszel_services", cutoff,
                                         ts_col="last_seen_ts")
    except Exception as e:  # noqa: BLE001
        print(f"[host_beszel_sampler] prune failed: {e}")
    return removed


async def _prune_old_rows() -> None:
    """Async wrapper — offloads to worker thread (same pattern as
    host_metrics_sampler) so large fleets don't block the event loop
    during the hourly prune. Routed through `prune_with_metrics` so
    the Stats → Samplers panel records the prune's row count + wall-
    clock duration."""
    from logic.sampler_metrics import prune_with_metrics
    await prune_with_metrics("host_beszel_sampler", _prune_old_rows_sync)


# noinspection DuplicatedCode,PyTypeChecker
async def host_beszel_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every
    ``tuning_stats_sample_interval_seconds``; dormant when ``beszel``
    isn't an active host-stats provider OR no curated host has
    ``beszel_name`` set.
    """
    print("[host_beszel_sampler] lifespan started")
    last_prune = 0.0
    iter_count = 0
    # Lazy-import so a future sampler that doesn't want the metrics
    # surface doesn't pay the cold-import cost. Resolves once at
    # lifespan start; the function reference is cached in `_record_tick`.
    from logic.sampler_metrics import record_tick as _record_tick
    try:
        while True:
            # Beszel-specific cadence wins when set; falls back to
            # the global stats cadence when 0 (the canonical
            # "inherit" sentinel — same fallback Pulse / Webmin
            # samplers use). 0 → 300s default keeps legacy
            # deployments unchanged.
            besz_interval = int(tuning.tuning_int(Tunable.BESZEL_SAMPLE_INTERVAL_SECONDS))
            interval = (besz_interval if besz_interval > 0
                        else int(tuning.tuning_int(Tunable.STATS_SAMPLE_INTERVAL_SECONDS))) or 300
            interval = max(30, interval)
            iter_count += 1
            # Unconditional per-iteration log — fires BEFORE the
            # active / curated gates so silent-sleep paths are
            # visible. Distinguishes "sampler not running" from
            # "sampler running but gated".
            active_set = _active_providers()
            print(
                f"[host_beszel_sampler] iter {iter_count}: "
                f"active={sorted(active_set)} interval={interval}s"
            )
            # Wall-clock the tick body so Stats → Samplers panel
            # surfaces per-tick duration trends. Wraps the same shape
            # the shared `lifespan_sampler_loop` helper uses; this
            # sampler can't trivially migrate to the helper (the
            # interval is recomputed per-tick from two interacting
            # tunables) so the metrics call is inlined.
            _tick_t0 = time.perf_counter()
            _tick_ok = True
            _tick_err = ""
            try:
                if "beszel" not in active_set:
                    print(f"[host_beszel_sampler] iter {iter_count} skip: beszel not in active")
                    await asyncio.sleep(interval)
                    continue
                hosts = _curated_beszel_hosts()
                if not hosts:
                    print(f"[host_beszel_sampler] iter {iter_count} skip: no curated beszel hosts")
                    await asyncio.sleep(interval)
                    continue
                hub_map = await _probe_one_tick()
                now = time.time()
                # Visibility log — operators chasing "why are
                # host_beszel_samples count=0 for my Beszel-enabled
                # hosts?" need to see whether the sampler IS ticking
                # AND how many hosts the per-tick lookup resolves.
                # One line per tick is reasonable at the 5-min default
                # cadence; if cadence ever drops to seconds we'd
                # rate-limit this. `lookups` is decremented per skip
                # so the operator can spot lookup-mismatch hosts.
                lookup_hits = 0
                rows: list[tuple] = []
                for h in hosts:
                    hid = h["id"]
                    bname = h["beszel_name"]
                    # Beszel's `lookup` helper tolerates case +
                    # whitespace on host-name keys, mirroring how
                    # `_merge_one_host` resolves Beszel data on the
                    # request path.
                    stats = _beszel.lookup(hub_map, bname) if hub_map else None
                    if not isinstance(stats, dict):
                        continue
                    lookup_hits += 1
                    row = _shape_row_for_db(hid, stats, now)
                    if row is not None:
                        rows.append(row)
                    # Persist per-unit service state for hosts whose
                    # Beszel agent tracks systemd units. Skips hosts
                    # without `host_services_raw` (agent not
                    # configured for systemd tracking) — silent
                    # no-op rather than emitting empty rows.
                    services_raw = stats.get("host_services_raw")
                    if isinstance(services_raw, list) and services_raw:
                        await _persist_services(hid, services_raw, now)
                if rows:
                    await _persist_tick(rows)
                # One-line tick summary so operators can confirm the
                # sampler is alive AND see where each host fell on the
                # lookup-vs-shape gate. `curated`=N hosts in the curated
                # pulse-enabled list; `hub_keys`=N hub-side host keys;
                # `looked_up`=N hosts that resolved via lookup;
                # `wrote`=N rows persisted (post-shape gate). If
                # `looked_up < curated`, lookup-key mismatch on the
                # missing hosts; if `wrote < looked_up`, no signal in
                # the matched stats dict.
                print(
                    f"[host_beszel_sampler] tick: curated={len(hosts)} "
                    f"hub_keys={len(hub_map or {})} "
                    f"looked_up={lookup_hits} wrote={len(rows)} "
                    f"interval={interval}s"
                )
                if (now - last_prune) > 3600:
                    await _prune_old_rows()
                    last_prune = now
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as e:  # noqa: BLE001
                _tick_ok = False
                _tick_err = type(e).__name__
                print(f"[host_beszel_sampler] tick error: {e}")
            finally:
                _record_tick(
                    "host_beszel_sampler",
                    (time.perf_counter() - _tick_t0) * 1000.0,
                    ok=_tick_ok,
                    error=_tick_err,
                )
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        print("[host_beszel_sampler] lifespan cancelled")
        raise


# ---- Read helpers (consumed by /api/hosts/history dispatch) -----

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
                "disk_total, disk_used, net_rx_bps, net_tx_bps, "
                "load_1m, load_5m, load_15m, "
                "swap_percent, swap_used, bandwidth, containers, "
                "temperatures_json, gpus_json "
                "FROM host_beszel_samples WHERE host_id=? AND ts >= ? "
                "ORDER BY ts ASC LIMIT ?",
                (host_id, int(since_ts), int(limit)),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[host_beszel_sampler] recent_samples({host_id!r}) failed: {e}")
        return []
    out: list[dict] = []
    import json as _json
    for r in rows:
        # Parse JSON-blob columns once on read; callers consume the
        # parsed shape directly. Failures decay to None / empty so a
        # malformed row doesn't poison the chart.
        temps = None
        if r[15]:
            try:
                temps = _json.loads(r[15])
            except (ValueError, TypeError):
                temps = None
        gpus = None
        if r[16]:
            try:
                gpus = _json.loads(r[16])
            except (ValueError, TypeError):
                gpus = None
        out.append({
            "ts": int(r[0]),
            "cpu_percent": (float(r[1]) if r[1] is not None else None),
            "mem_total": (int(r[2]) if r[2] is not None else None),
            "mem_used": (int(r[3]) if r[3] is not None else None),
            "disk_total": (int(r[4]) if r[4] is not None else None),
            "disk_used": (int(r[5]) if r[5] is not None else None),
            "net_rx_bps": (float(r[6]) if r[6] is not None else None),
            "net_tx_bps": (float(r[7]) if r[7] is not None else None),
            "load_1m": (float(r[8]) if r[8] is not None else None),
            "load_5m": (float(r[9]) if r[9] is not None else None),
            "load_15m": (float(r[10]) if r[10] is not None else None),
            "swap_percent": (float(r[11]) if r[11] is not None else None),
            "swap_used": (float(r[12]) if r[12] is not None else None),
            "bandwidth": (float(r[13]) if r[13] is not None else None),
            "containers": (int(r[14]) if r[14] is not None else None),
            "temperatures": temps,
            "gpus": gpus,
        })
    return out


# noinspection DuplicatedCode,PyTypeChecker
def history_series(host_id: str, hours: int) -> list[dict]:
    """Beszel-compatible series envelope so the SPA's chart helpers
    work against the local-table path with no branching.

    Mirrors ``host_pulse_sampler.history_series``'s shape AND
    ``logic/beszel.py:fetch_system_history``'s synthesised
    aggregates per-point (``gpu_pwr`` / ``gpu_usage`` / ``gpu_vram_*`` /
    ``temp_max`` / ``la*_pct``). The drawer chart cards bind to those
    scalar keys directly (e.g. GPU Power Draw card reads ``gpu_pwr``,
    NOT the per-GPU ``gpus`` array) — without the synthesis the chart
    falls through to the "Collecting data" placeholder permanently
    even when ``gpus`` carries valid per-tick data. The sampler stores
    parsed payloads (``temps`` dict, ``gpus`` list) so the synthesis
    is a pure read-time computation; no schema change required.
    """
    hours = max(1, min(168, int(hours or 1)))
    since = int(time.time() - hours * 3600)
    raw = recent_samples(host_id, since, limit=hours * 60)
    gib = 1024 ** 3
    # Cores best-effort from host_snapshots — used to convert raw load
    # averages (0.18 / 1.21 / etc.) into the chart's percent-of-cores
    # variant. Falls back to 1 when the snapshot row is missing OR the
    # field is absent; the chart's `la*_pct` series then equals the
    # raw load value × 100, capped at 100. Read once per call (not
    # per-point) — cores doesn't change inside the chart window.
    cores = 1
    try:
        from logic.gather import load_host_snapshots as _load_snaps
        _snaps = _load_snaps()
        _snap = _snaps.get(host_id) or {}
        _c = _snap.get("host_cores") or _snap.get("cores")
        if _c:
            cores = max(1, int(_c))
    except (ImportError, ValueError, TypeError, OSError):
        cores = 1
    series: list[dict] = []
    for r in raw:
        mem_total = r.get("mem_total") or 0
        mem_used = r.get("mem_used") or 0
        disk_total = r.get("disk_total") or 0
        disk_used = r.get("disk_used") or 0
        nr = r.get("net_rx_bps") or 0.0
        ns = r.get("net_tx_bps") or 0.0
        net = nr + ns
        # GPU per-tick aggregates. Mirrors logic/beszel.py:fetch_system_history.
        # `host_gpus` from the merged stats dict carries
        # `{name, power_watts, usage_percent, vram_used_bytes,
        # vram_total_bytes}` per GPU. Average power + usage across
        # GPUs (so multi-GPU rigs render one tidy line per metric);
        # sum vram for the combined-pressure view. Missing fields →
        # 0 → chart hides on the existing `host_gpus.length > 0`
        # outer gate, which is unaffected here.
        gpus = r.get("gpus") or []
        gpu_pwr_sum = 0.0
        gpu_usage_sum = 0.0
        gpu_vram_used_sum = 0
        gpu_vram_total_sum = 0
        gpu_n = 0
        for _g in gpus:
            if not isinstance(_g, dict):
                continue
            try:
                gpu_pwr_sum += float(_g.get("power_watts") or 0)
            except (TypeError, ValueError):
                pass
            try:
                gpu_usage_sum += float(_g.get("usage_percent") or 0)
            except (TypeError, ValueError):
                pass
            try:
                gpu_vram_used_sum += int(_g.get("vram_used_bytes") or 0)
            except (TypeError, ValueError):
                pass
            try:
                gpu_vram_total_sum += int(_g.get("vram_total_bytes") or 0)
            except (TypeError, ValueError):
                pass
            gpu_n += 1
        gpu_pwr_avg = (gpu_pwr_sum / gpu_n) if gpu_n else 0.0
        gpu_usage_avg = (gpu_usage_sum / gpu_n) if gpu_n else 0.0
        gpu_vram_pct = (
            (gpu_vram_used_sum / gpu_vram_total_sum * 100.0)
            if gpu_vram_total_sum else 0.0
        )
        # Per-tick peak across temperature sensors — chart binds to
        # the scalar `temp_max`; the full `temps` dict rides alongside
        # for the legend display.
        temps = r.get("temperatures") or {}
        temp_max = 0.0
        if isinstance(temps, dict) and temps:
            try:
                temp_max = max(float(v) for v in temps.values() if v is not None)
            except (TypeError, ValueError):
                temp_max = 0.0
        la1_raw = r.get("load_1m") or 0.0
        la5_raw = r.get("load_5m") or 0.0
        la15_raw = r.get("load_15m") or 0.0
        series.append({
            "t": r["ts"],
            "cpu": r.get("cpu_percent") or 0.0,
            "mp": (100.0 * mem_used / mem_total) if mem_total else 0.0,
            "dp": (100.0 * disk_used / disk_total) if disk_total else 0.0,
            "mu": (mem_used / gib) if mem_used else 0.0,
            "du": (disk_used / gib) if disk_used else 0.0,
            "b": r.get("bandwidth") or net,
            "nr": nr,
            "ns": ns,
            "net": net,
            # No per-disk I/O on Beszel — leave 0 (drawer card hides
            # via the existing `maxRaw > 0` gate).
            "dr": 0.0,
            "dw": 0.0,
            # Load avg raw + percent-of-cores variants. Drawer Load
            # chart prefers `la*_pct` (0..100 rendering) when present
            # and falls back to raw `la*` otherwise.
            "la1": la1_raw,
            "la5": la5_raw,
            "la15": la15_raw,
            "la1_pct": min(100.0, (la1_raw / cores) * 100.0),
            "la5_pct": min(100.0, (la5_raw / cores) * 100.0),
            "la15_pct": min(100.0, (la15_raw / cores) * 100.0),
            "s": r.get("swap_percent") or 0.0,
            "su": r.get("swap_used") or 0.0,
            # Temperatures / GPUs ride alongside as parsed payloads
            # so the SPA can render the dedicated chart cards
            # without a second fetch. Empty / null when the agent
            # didn't emit them.
            "temps": temps,
            "gpus": gpus,
            # Synthesised aggregates the SPA chart cards bind to
            # directly. Missing → 0 → chart shows "Collecting data"
            # via the gate `hostChartMax(...) > 0`.
            "temp_max": temp_max,
            "gpu_pwr": gpu_pwr_avg,
            "gpu_usage": gpu_usage_avg,
            "gpu_vram_pct": gpu_vram_pct,
            "gpu_vram_used_bytes": gpu_vram_used_sum,
            "gpu_vram_total_bytes": gpu_vram_total_sum,
        })
    return series


# noinspection DuplicatedCode
def services_for_host(host_id: str) -> list[dict]:
    """Return the per-unit service snapshot for one host.

    Mirrors the `_services_summary` shape but with each individual
    unit exposed: ``[{name, state, sub_state, last_seen_ts,
    last_change_ts}, ...]`` ordered with FAILED units first
    (state=3), then by name.
    """
    if not host_id:
        return []
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT service_name, state, sub_state, "
                "last_seen_ts, last_change_ts "
                "FROM host_beszel_services WHERE host_id = ? "
                "ORDER BY (state = 3) DESC, service_name ASC",
                (host_id,),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[host_beszel_sampler] services_for_host({host_id!r}) failed: {e}")
        return []
    out: list[dict] = []
    for r in rows:
        out.append({
            "name": str(r[0] or ""),
            "state": (int(r[1]) if r[1] is not None else None),
            "sub_state": (int(r[2]) if r[2] is not None else None),
            "last_seen_ts": int(r[3] or 0),
            "last_change_ts": int(r[4] or 0),
        })
    return out


async def refresh_services_now(host_id: str) -> list[dict]:
    """On-demand LIVE re-probe of one host's Beszel systemd services.

    Runs a single ``probe_hub()`` fetch, persists the matched host's
    per-unit service rows into ``host_beszel_services``, then returns the
    fresh ``services_for_host(host_id)``. Backs the drawer's "refresh
    services" action so an operator who just fixed a failed unit sees it
    flip to active WITHOUT waiting for the next sampler tick (the table is
    otherwise only as fresh as the last tick, which can be minutes old).

    A hub probe covers EVERY host in one call, so this also refreshes the
    other hosts' rows as a side-effect — cheap + harmless. Best-effort:
    any probe failure falls back to the cached table read (never raises).
    """
    hid = (host_id or "").strip()
    if not hid:
        return []
    try:
        if "beszel" in _active_providers():
            row = next((h for h in _curated_beszel_hosts() if h.get("id") == hid), None)
            bname = (row or {}).get("beszel_name") or ""
            if bname:
                hub_map = await _probe_one_tick()
                stats = _beszel.lookup(hub_map, bname) if hub_map else None
                if isinstance(stats, dict):
                    services_raw = stats.get("host_services_raw")
                    if isinstance(services_raw, list) and services_raw:
                        await _persist_services(hid, services_raw, time.time())
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[host_beszel_sampler] refresh_services_now({hid!r}) failed: {e}")
    return services_for_host(hid)


def last_samples(host_id: str, limit: int = 5) -> list[dict]:
    """Newest-first recent rows for the debug endpoint. Mirrors
    ``host_pulse_sampler.last_samples``'s contract.
    """
    if not host_id:
        return []
    # Reuse `recent_samples` with a wide ts window — same column set,
    # parses chart-extras JSON the same way, just sorted oldest-first.
    # Reverse on return for newest-first contract.
    rows = recent_samples(host_id, since_ts=0, limit=int(limit))
    return list(reversed(rows))
