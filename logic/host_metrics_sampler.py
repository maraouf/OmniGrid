"""Per-host historical CPU / Memory / Disk / Network metrics sampler.

Sibling of :mod:`logic.host_net_sampler` — same architectural shape, same
skip-don't-synthesize discipline (see the project conventions "Counter-rate samplers
must SKIP, not synthesize"), but covers the broader gauge metrics
(cpu_percent, mem_used/total, disk_used/total) plus net rx/tx rates so
node-exporter-only hosts (no Beszel agent) get a usable historical
charts surface in the host drawer.

Why a separate module from :mod:`logic.host_net_sampler`:
  - That sampler writes ONE metric pair (rx_bytes_per_s / tx_bytes_per_s)
    derived from monotonic counters; this one writes a denser row of
    point-in-time gauges PLUS the same net rates. Different schema, same
    cadence, same lifespan-task contract. Two siblings is fine; if a
    third lands, refactor to a shared base.
  - The net sampler exists primarily as a Beszel FALLBACK (patches an
    existing Beszel-derived series when the agent is misconfigured);
    this sampler exists as a Beszel SUBSTITUTE for hosts that never had
    a Beszel agent at all. Different consumer, different contract.

Counter rates (net_rx_bps / net_tx_bps) follow the same sanity bounds
host_net_sampler uses: ``60 ≤ Δs ≤ 900`` AND ``0 ≤ Δbytes ≤ 10 GB``. An
out-of-bounds delta SKIPS the row entirely rather than INSERT a 0 — a
stored zero would mask the very signal the chart should surface (host
reboot / counter wrap / clock skew). Gauges (CPU%, mem, disk) don't need
delta bounds because they're point-in-time, but they DO honour an
``is_meaningful`` check so a missing metric doesn't poison the series.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from typing import Any, Optional

import httpx

# Numeric-coercion helpers live in the dependency-free logic.coerce leaf
# module now; aliased to the legacy underscore names so call sites are
# unchanged. (Consolidated from the previously per-sampler duplicates.)
from logic.coerce import (  # noqa: E402
    safe_int as _safe_int,
    safe_float as _safe_float,
    int_or_none as _int_or_none,
    float_or_none as _float_or_none,
    as_dict,
)


def _coerce_counter_pair(a_raw: Any, b_raw: Any) -> Optional[tuple[int, int]]:
    """Coerce a paired counter (net rx/tx OR disk read/written) to a
    `(int, int)` tuple, or return None when either value is missing
    / unparseable. Used by :func:`_compute_row` for the net + disk
    counter blocks — both follow the same pattern of "if both parse,
    advance; otherwise drop the pair entirely so we never store a
    half-real signal."""
    a = _safe_int(a_raw, -1)
    b = _safe_int(b_raw, -1)
    if a < 0 or b < 0:
        return None
    return a, b


from logic import node_exporter as _ne  # noqa: E402
from logic import tuning  # noqa: E402
from logic.tuning import Tunable  # noqa: E402
from logic.db import db_conn  # noqa: E402

# Sanity bounds — same values, same rationale as host_net_sampler.
_MIN_DELTA_SECONDS = 60
_MAX_DELTA_SECONDS = 900
_MIN_DELTA_BYTES = 0
_MAX_DELTA_BYTES = 10 * 1024 * 1024 * 1024  # 10 GB

# Per-host previous absolute counters for delta math (net rx / net tx /
# disk read / disk written / cpu_total / cpu_idle). Lives across ticks
# within one sampler-task lifetime; cleared on lifespan cancel/restart
# so the post-restart first delta is correctly SKIPPED. Tuple grew over
# time:
# pre-fix: (ts, rx, tx) — 3 elements
#:     (ts, rx, tx, dr, dw) — 5 elements (disk added)
#:     (ts, rx, tx, dr, dw, cpu_total, cpu_idle) — 7 elements
# In-memory cache is restart-only so no migration needed; the
# `_compute_row` decoder tolerantly len()-checks `prev`.
_last_counters: dict[str, tuple] = {}  # host_id → variable-length tuple

# concurrency cap is operator-tunable via
# `tuning_host_metrics_probe_concurrency` (default 8, range 1-64).
# Resolved per-tick at the consumer below; module-level constant
# removed so a tunable change takes effect on the very next tick.


# Active-providers parser + curated-hosts walker live in logic/db.py —
# single source of truth shared with main.py / gather.py / both
# samplers .
from logic.db import (  # noqa: E402
    active_host_stats_providers as _active_providers,
    curated_ne_hosts as _load_curated_hosts,
    curated_snmp_hosts as _load_curated_snmp_hosts,
    get_setting as _get_setting,
    iter_curated_hosts,
    prune_rows_older_than,
)
from logic.settings_keys import Settings  # noqa: E402

# Canonical strict-positive helper lives in logic/merge.py — alias it
# locally so existing call sites stay readable. Kept as a thin alias
# rather than a re-import-everywhere refactor; the behaviour contract
# is identical.
from logic.merge import is_positive_number as _is_meaningful_number  # noqa: E402


def _delta_seconds_ok(delta_seconds: float) -> bool:
    """True when a counter-rate time delta is in-bounds (skip-don't-synthesize:
    reject clock skew / long outages outside the sane window)."""
    return _MIN_DELTA_SECONDS <= delta_seconds <= _MAX_DELTA_SECONDS


def _delta_bytes_ok(delta_bytes: int) -> bool:
    """True when a counter byte delta is in-bounds (reject negative / rollover /
    absurd jumps so a reboot or wrap isn't stored as a spike)."""
    return _MIN_DELTA_BYTES <= delta_bytes <= _MAX_DELTA_BYTES


def _sanity_ok(delta_seconds: float, delta_rx: int, delta_tx: int) -> bool:
    """Net-pair sanity check (kept for the existing call sites). Returns
    True only when BOTH rx AND tx deltas are in bounds — a single
    out-of-bounds field skips the whole pair so the rates stay paired."""
    if not _delta_seconds_ok(delta_seconds):
        return False
    return _delta_bytes_ok(delta_rx) and _delta_bytes_ok(delta_tx)


def _compute_row(
    host_id: str,
    now: float,
    stats: dict,
    prev: Optional[tuple],
) -> tuple[Optional[dict], Optional[tuple[float, int, int, int, int, float, float]]]:
    """Pure-function core: turn a probe payload + previous counters into
    an INSERT-shaped row plus the next-tick counter cache.

    Returns ``(row_or_none, next_counter_or_none)``. The row is None when
    every gauge AND every rate are unmeaningful — there's nothing worth
    persisting. The next_counter is None when the current probe didn't
    return rx/tx counters at all (don't poison the cache with a zeroed
    entry — next tick should still treat itself as "first"). When net
    counters ARE present but disk counters AREN'T (older NE without the
    diskstats collector), the cache stores zeros for the missing pair —
    the disk-rate path is gated separately so a missing field stays NULL.

    Skip rules:
      - Net rate pair: SKIP (NULL both) when delta seconds out of bounds
        OR when either of the byte deltas is out of bounds OR no previous
        sample.
      - Disk rate pair: SAME, but evaluated independently of net (a host
        with stable net but rebooted disk counters keeps its net rates).
      - Gauges: SKIP an individual field (store NULL) when not meaningful.
        Whole row is dropped only when every field would be NULL.

    Backwards compatibility: ``prev`` may be the legacy 3-tuple
    ``(ts, rx, tx)`` from a process that started shipped.
    In-memory cache is wiped on restart so this only matters mid-process
    if a partial reload happened — handled by len()-checking ``prev``.
    """
    # Gauges — pull what node-exporter parsed; treat 0 / None as missing.
    mem_total = int(stats.get("host_mem_total") or 0)
    mem_used = int(stats.get("host_mem_used") or 0)
    disk_total = int(stats.get("host_disk_total") or 0)
    disk_used = int(stats.get("host_disk_used") or 0)

    cpu_percent: Optional[float] = None
    raw_cpu = stats.get("host_cpu_percent")
    if _is_meaningful_number(raw_cpu):
        cpu_percent = float(raw_cpu)  # type: ignore[arg-type]  # _is_meaningful_number narrows to numeric

    # CPU-seconds counters for delta-derived %CPU on NE-only hosts.
    # Sum across all CPUs all modes for `total`; only mode=idle for `idle`.
    # %CPU = 100 * (1 - (delta_idle / delta_total)).
    cpu_total_secs = stats.get("host_cpu_seconds_total") or 0
    cpu_idle_secs = stats.get("host_cpu_seconds_idle") or 0
    have_cpu_counters = cpu_total_secs > 0

    # Net counters — required to advance the cache. Coerce to 0 when
    # missing so the int() calls below stay well-typed for pyright
    # (int(None) would TypeError; we already gate on `have_net_counters`
    # before consuming the rx/tx values downstream).
    rx_total = stats.get("host_net_rx_total")
    tx_total = stats.get("host_net_tx_total")
    have_net_counters = (rx_total is not None) and (tx_total is not None)

    # Disk counters — independent of net; some exporters disable
    # diskstats, in which case rates stay NULL but net keeps working.
    dr_total = stats.get("host_disk_read_total")
    dw_total = stats.get("host_disk_write_total")
    have_disk_counters = (dr_total is not None) and (dw_total is not None)

    rx_rate: Optional[float] = None
    tx_rate: Optional[float] = None
    dr_rate: Optional[float] = None
    dw_rate: Optional[float] = None
    next_counter: Optional[tuple[float, int, int, int, int, float, float]] = None

    rx = tx = dr = dw = 0
    if have_net_counters and rx_total is not None and tx_total is not None:
        _net = _coerce_counter_pair(rx_total, tx_total)
        if _net is None:
            have_net_counters = False
        else:
            rx, tx = _net
    if have_disk_counters and dr_total is not None and dw_total is not None:
        _disk = _coerce_counter_pair(dr_total, dw_total)
        if _disk is None:
            have_disk_counters = False
        else:
            dr, dw = _disk

    # Cache the current counters (zeros where unavailable) so the next
    # tick has a baseline. A rejected delta still advances the cache so
    # we don't keep diffing against a stale anchor forever.
    if have_net_counters or have_disk_counters or have_cpu_counters:
        next_counter = (now, rx, tx, dr, dw, float(cpu_total_secs), float(cpu_idle_secs))

    # Decompose `prev` tolerantly — earliest entries were 3-tuples;
    # the disk-counter expansion grew the shape to 5-tuple; the CPU-
    # seconds expansion grew it again to 7-tuple. The cache is
    # restart-only so older shapes only matter mid-process if a
    # partial reload happened — handled by len()-checking ``prev``.
    # Decompose with concrete narrow types so the delta math below stays
    # pyright-clean (tuple elements are typed Any otherwise).
    prev_ts: Optional[float] = None
    prev_rx: Optional[int] = None
    prev_tx: Optional[int] = None
    prev_dr: Optional[int] = None
    prev_dw: Optional[int] = None
    prev_cpu_total: Optional[float] = None
    prev_cpu_idle: Optional[float] = None
    if prev is not None:
        if len(prev) >= 3:
            prev_ts = _safe_float(prev[0])
            prev_rx = _safe_int(prev[1])
            prev_tx = _safe_int(prev[2])
        if len(prev) >= 5:
            prev_dr = _safe_int(prev[3])
            prev_dw = _safe_int(prev[4])
        if len(prev) >= 7:
            prev_cpu_total = _safe_float(prev[5])
            prev_cpu_idle = _safe_float(prev[6])

    if prev_ts is not None:
        delta_s = now - prev_ts
        if _delta_seconds_ok(delta_s):
            # Net rate pair — both fields must be in bounds.
            if have_net_counters and prev_rx is not None and prev_tx is not None:
                d_rx = rx - prev_rx
                d_tx = tx - prev_tx
                if _delta_bytes_ok(d_rx) and _delta_bytes_ok(d_tx):
                    rx_rate = d_rx / delta_s
                    tx_rate = d_tx / delta_s
            # Disk rate pair — evaluated INDEPENDENTLY of net.
            if have_disk_counters and prev_dr is not None and prev_dw is not None:
                d_dr = dr - prev_dr
                d_dw = dw - prev_dw
                if _delta_bytes_ok(d_dr) and _delta_bytes_ok(d_dw):
                    dr_rate = d_dr / delta_s
                    dw_rate = d_dw / delta_s
            # CPU-seconds delta. Skip when:
            # - no previous CPU sample (first tick / restart),
            # - delta_total <= 0 (clock skew / counter reset),
            # - delta_idle < 0 (counter reset → bogus negative %).
            # Result clamped to [0, 100] so a mid-tick clock blip
            # can't surface 137% CPU on the chart.
            if (have_cpu_counters
                and prev_cpu_total is not None and prev_cpu_idle is not None
                and cpu_percent is None):
                d_total = _safe_float(cpu_total_secs) - prev_cpu_total
                d_idle = _safe_float(cpu_idle_secs) - prev_cpu_idle
                if d_total > 0 and d_idle >= 0:
                    pct = 100.0 * (1.0 - (d_idle / d_total))
                    cpu_percent = max(0.0, min(100.0, pct))

    # If literally nothing meaningful — drop the row.
    nothing_to_write = (
        cpu_percent is None
        and not _is_meaningful_number(mem_used)
        and not _is_meaningful_number(mem_total)
        and not _is_meaningful_number(disk_used)
        and not _is_meaningful_number(disk_total)
        and rx_rate is None
        and tx_rate is None
        and dr_rate is None
        and dw_rate is None
    )
    if nothing_to_write:
        return None, next_counter

    row = {
        "ts": int(now),
        "host_id": host_id,
        "cpu_percent": cpu_percent,
        "mem_used": mem_used if _is_meaningful_number(mem_used) else None,
        "mem_total": mem_total if _is_meaningful_number(mem_total) else None,
        "disk_used": disk_used if _is_meaningful_number(disk_used) else None,
        "disk_total": disk_total if _is_meaningful_number(disk_total) else None,
        "net_rx_bps": rx_rate,
        "net_tx_bps": tx_rate,
        "disk_read_bps": dr_rate,
        "disk_write_bps": dw_rate,
    }
    return row, next_counter


# Permanent-fail tracking helpers. Single source of truth lives
# in the host_failure_state table; the sampler reads on entry to
# short-circuit paused hosts AND writes on every probe outcome to
# advance the counter / clear-on-success / auto-pause when the failure
# window is exceeded.
def _resolve_target_for_log(host_id: str) -> str:
    """Resolve the operator-visible target (FQDN / IP) for a host id
    by walking iter_curated_hosts(). Used by failure-state error log
    paths so a `Cannot operate on...` / `failure-state write error:`
    line surfaces WHICH host the curated row is pointing at — the
    bare host_id is often a short alias the operator may not
    immediately recognise. Best-effort: returns empty string on any
    lookup failure (the caller falls back to the bare host_id). The
    chain mirrors the canonical address-fallback contract documented
    in the project conventions: address → ssh.fqdn → ssh.host → SKIP."""
    try:
        for _h in iter_curated_hosts():
            if (_h.get("id") or "").strip() != host_id:
                continue
            _ssh = as_dict(_h.get("ssh"))
            return (
                (_h.get("address") or "").strip()
                or (_ssh.get("fqdn") or "").strip()
                or (_ssh.get("host") or "").strip()
                or ""
            )
    except (sqlite3.Error, OSError, RuntimeError,
            AttributeError, KeyError, TypeError, ValueError):
        # Best-effort target resolution — a DB read error from
        # iter_curated_hosts() or a malformed hosts_config row falls back to
        # the bare host_id. Sync helper, so no CancelledError to carve out.
        pass
    return ""


def _log_label_with_target(host_id: str, provider: str = "") -> str:
    """Combine the canonical `<provider>:<host_id>` (or bare host_id)
    label with the resolved target FQDN/IP for the error-log paths.
    Format: `node_exporter:pihole2(target=pihole2.example.com)`.
    Empty target → falls back to the bare label so logs stay quiet
    on hosts the resolver can't find."""
    label = f"{provider}:{host_id}" if provider else host_id
    target = _resolve_target_for_log(host_id)
    if target and target != host_id:
        return f"{label}(target={target})"
    return label


def _get_failure_state(host_id: str, provider: str = "") -> Optional[dict]:
    """Read the host_failure_state row for ``(host_id, provider)``.

    Column-parity with ``main._failure_state_for_host`` so internal
    consumers (auto-pause logic, future "auto-resume after N hours of
    inactivity" features) see the same shape the API surface does.
    Default ``provider=''`` reads the whole-host row (legacy bare-id
    convention, now stored as the empty-string provider after migration 2).
    """
    try:
        with db_conn() as c:
            cur = c.execute(
                "SELECT first_failure_ts, consecutive_failures, paused, "
                "paused_at, last_error, last_failure_ts "
                "FROM host_failure_state WHERE host_id = ? AND provider = ?",
                (host_id, provider),
            )
            row = cur.fetchone()
    # noinspection PyBroadException
    except Exception as e: # noqa: BLE001
        print(f"[host_metrics_sampler] {_log_label_with_target(host_id, provider)!r} "
              f"failure-state read error: {e}")
        return None
    if row is None:
        return None
    return {
        "first_failure_ts": row[0],
        "consecutive_failures": row[1],
        "paused": bool(row[2]),
        "paused_at": row[3],
        "last_error": row[4],
        # Falls back to first_failure_ts on rows that pre-date the
        # column add (the first probe failure on the new schema
        # overwrites the NULL via _record_failure).
        "last_failure_ts": (row[5] if (len(row) > 5 and row[5] is not None) else row[0]),
    }


# Frozen set of provider names that have the auto-pause contract.
# CANONICAL source of truth — `main.py` imports this set (aliased as
# `_PROVIDER_AUTO_PAUSE_NAMES`) so a seventh provider is a one-line
# edit here. Could be derived from `tuning.TUNABLES` in a future
# refactor by walking the `tuning_<provider>_failure_pause_rounds`
# keys, but the literal set is cheaper to read at module load and
# the duplication risk is gone.
_PROVIDER_PREFIXES = frozenset((
    "beszel", "pulse", "node_exporter", "webmin", "ping", "snmp", "http_probe",
    # service_probe is per-chip on `services[]`, but the per-(host)
    # auto-pause rollup uses the same `host_failure_state` row shape
    # as the other providers — so it belongs in this set.
    "service_probe",
))
# Public alias for cross-module use. main.py imports as
# `_PROVIDER_AUTO_PAUSE_NAMES` per the project conventions ("Vendor / capability key
# sets need ONE source of truth"); exposing without the leading
# underscore satisfies the IDE's protected-member check without
# duplicating the literal.
PROVIDER_PREFIXES = _PROVIDER_PREFIXES

# ---------------------------------------------------------------------------
# Defensive "is this provider configured for this host" cache.
#
# Used by `record_provider_outcome` to refuse FAILURE recording for a
# (host, provider) pair where the curated row doesn't have the matching
# *_name / opt-in flag. Operator-reported pattern: the SPA's per-host
# probe gate at `_merge_one_host` is correct, but if the curated config
# silently carries a stale `beszel_name` (or any other provider mapping
# the operator thought they cleared), the next probe cycle records a
# real failure → eventually crosses the threshold → fires an email
# pause-notification for a provider the operator says they "don't have
# set up". The defence-in-depth: even if the call site forgot the
# pre-record gate, the recorder itself refuses to write for a host that
# isn't configured for that provider. Side effect: also cleans the
# legacy paused state on first refusal so the orphan self-heals without
# requiring a save / redeploy / manual resume.
#
# Cache invalidates on every hosts_config save (`api_hosts_config_set`
# calls `_invalidate_host_provider_config_cache()` below) so a fresh
# mapping takes effect on the very next probe.
_HOST_PROVIDER_CONFIG_CACHE: dict[str, set[str]] | None = None
_HOST_PROVIDER_CONFIG_CACHE_TS: float = 0.0


# Cache TTL — even if invalidate isn't called, refresh from disk after
# this many seconds. Belt-and-braces against any future code path that
# writes hosts_config without going through the canonical save endpoint.
# Read via `tuning_int(Tunable.HOST_PROVIDER_CONFIG_CACHE_TTL_SECONDS)`
# at the per-use site below — per-use respects the No-static-config
# rule (operator can lower the TTL via Admin → Config to land the
# next save faster, or raise it on a stable fleet to lighten DB reads).


def _invalidate_host_provider_config_cache() -> None:
    """Drop the cached host→{providers} map so the next failure-record
    refresh from disk. Call after any hosts_config save."""
    global _HOST_PROVIDER_CONFIG_CACHE, _HOST_PROVIDER_CONFIG_CACHE_TS
    _HOST_PROVIDER_CONFIG_CACHE = None
    _HOST_PROVIDER_CONFIG_CACHE_TS = 0.0


# Public alias for cross-module use (main.py invalidates after a
# `hosts_config` save).
invalidate_host_provider_config_cache = _invalidate_host_provider_config_cache


def _host_provider_config() -> dict[str, set[str]]:
    """Return ``{host_id: {provider, ...}}`` from the curated config.

    Reads `settings.hosts_config` lazily and caches for ~60s. Mirrors the
    same six-bool detection `_sweep_orphan_provider_state_rows` uses so
    the two stay in lock-step.
    """
    global _HOST_PROVIDER_CONFIG_CACHE, _HOST_PROVIDER_CONFIG_CACHE_TS
    now_ts = time.time()
    # Per-use TTL read — admin can dial via Admin → Config without
    # restart. Defensive fallback to legacy 60s if `tuning_int` raises
    # (corrupt DB row).
    try:
        _ttl = float(tuning.tuning_int(Tunable.HOST_PROVIDER_CONFIG_CACHE_TTL_SECONDS))
    except (KeyError, ValueError, TypeError):
        _ttl = 60.0
    if (
        _HOST_PROVIDER_CONFIG_CACHE is not None
        and (now_ts - _HOST_PROVIDER_CONFIG_CACHE_TS) < _ttl
    ):
        return _HOST_PROVIDER_CONFIG_CACHE
    out: dict[str, set[str]] = {}
    try:
        # JSON-parse + isinstance + enabled-gate + id-empty filtering is
        # delegated to `iter_curated_hosts`. Per-provider field
        # filtering stays here because the SNMP gate is non-trivial
        # (snmp_name OR address AND snmp.enabled).
        for h in iter_curated_hosts():
            hid = (h.get("id") or "").strip()  # iter_curated_hosts already guarantees non-empty
            configured: set[str] = set()
            if (h.get("beszel_name") or "").strip():
                configured.add("beszel")
            if (h.get("pulse_name") or "").strip():
                configured.add("pulse")
            if (h.get("ne_url") or "").strip():
                configured.add("node_exporter")
            # Webmin is configured when EITHER `webmin_name` (alias
            # lookup against `webmin_aliases`) OR the per-host
            # `webmin_url` is non-empty. Mirrors the resolver chain in
            # `_merge_one_host` (`webmin_aliases[id] → h["webmin_url"]`
            # → SKIP). Pre-fix this only checked `webmin_name`, so a
            # host that resolved through `webmin_url` was treated as
            # "Webmin not configured" — `record_provider_outcome`'s
            # defensive guard then DELETED the per-(webmin, host) last
            # OK + failure-state rows on the next failure, hiding the
            # chip subtitle forever AND preventing auto-pause from
            # tripping. Same drift class as the SNMP fix below.
            if (h.get("webmin_name") or "").strip() or (h.get("webmin_url") or "").strip():
                configured.add("webmin")
            if bool((h.get("ping") or {}).get("enabled", False)):
                configured.add("ping")
            # SNMP is configured when EITHER `snmp_name` OR the
            # shared `address` field is non-empty AND `snmp.enabled`
            # is True. Mirrors the resolver chain in
            # `_probe_one_snmp` and `_merge_one_host` (aliases →
            # snmp_name → address → SKIP). Pre-fix this only
            # checked `snmp_name`, so a host that cleared
            # `snmp_name` intending to use the shared `address`
            # was incorrectly treated as "SNMP not configured" —
            # `record_provider_outcome`'s defensive guard then
            # refused to record failures AND deleted any pre-
            # existing failure-state + last_ok rows for that host,
            # making the chip subtitle "Updated Xm ago" disappear.
            snmp_target_present = (
                (h.get("snmp_name") or "").strip()
                or (h.get("address") or "").strip()
            )
            if snmp_target_present and (
                isinstance(h.get("snmp"), dict) and h["snmp"].get("enabled") is True
            ):
                configured.add("snmp")
            # http_probe + service_probe are master-toggle providers with
            # per-host opt-in (NOT host_stats_source CSV members). They MUST
            # be detected here too: when an http/service probe fails,
            # `record_provider_outcome`'s defensive guard checks
            # `provider not in cfg[host_id]` and — if the provider is missing
            # from this set — DELETES the host_provider_last_ok row, making
            # the chip's "Updated Xm ago" subtitle disappear. Same failure
            # class the SNMP comment above describes. http_probe is configured
            # when the per-host `http_probe.enabled` flag is on; service_probe
            # when ANY curated service chip has `probe.enabled`.
            if isinstance(h.get("http_probe"), dict) and h["http_probe"].get("enabled") is True:
                configured.add("http_probe")
            svc_list = h.get("services")
            if isinstance(svc_list, list) and any(
                isinstance(svc, dict) and isinstance(svc.get("probe"), dict)
                and svc["probe"].get("enabled") is True
                for svc in svc_list
            ):
                configured.add("service_probe")
            out[hid] = configured
    # noinspection PyBroadException
    except Exception as e: # noqa: BLE001
        # DB read failure is non-fatal — fall back to "every provider
        # configured" (legacy behaviour) so a transient blip doesn't
        # silently disable failure recording for a real outage.
        print(f"[host_metrics_sampler] host_provider_config refresh failed: {e}")
        return {}
    _HOST_PROVIDER_CONFIG_CACHE = out
    _HOST_PROVIDER_CONFIG_CACHE_TS = now_ts
    return out


# noinspection DuplicatedCode
async def _record_failure(
    host_id: str, now: float, error: str,
    *, round_threshold: Optional[int] = None,
    provider: str = "",
) -> None:
    """Increment the failure counter, stamp first_failure_ts on the
    first failure of a new streak, auto-pause when the threshold is
    exceeded. Called from `_probe_one` whenever a probe attempt fails
    (network error OR exporter_error response).

    Two pause-trigger modes:
      - Default (NE / Webmin / etc.): time-window based. Pauses after
        ``tuning_host_permanent_fail_window_seconds`` of consecutive
        failures. Operator-friendly when probe cadence is fixed.
      - ``round_threshold`` (SNMP): pauses after N consecutive failed
        rounds regardless of wall-clock. Operator-friendly when probe
        cadence varies (SNMP printers may poll hourly, switches every
        minute — "5 rounds" means the same UX commitment in both cases).
        Pass 0 to disable auto-pause entirely (just record the failure).

    ``provider`` selects the (host_id, provider) row in the composite-PK
    table after migration: ``''`` for whole-host pauses, ``'snmp'``
    / ``'webmin'`` / etc. for per-(provider, host) pauses.

    Async because the auto-pause transition fires an Apprise
    notification via ``asyncio.create_task`` — ``create_task`` requires
    a running event loop in scope, which we get for free inside an
    async function.
    """
    # Three-tier lookup via the unified Tuning Config : DB > env >
    # default. ``tuning.tuning_int`` always returns at least the code
    # default, so a fallback here is dead code
    try:
        window = int(tuning.tuning_int(Tunable.HOST_PERMANENT_FAIL_WINDOW_SECONDS))
    except (KeyError, ValueError, TypeError):
        window = 900
    if window < 60:
        window = 60
    err_short = (error or "").strip()[:500]
    bare_host = host_id
    log_label = f"{provider}:{host_id}" if provider else host_id
    try:
        with db_conn() as c:
            cur = c.execute(
                "SELECT first_failure_ts, consecutive_failures, paused "
                "FROM host_failure_state WHERE host_id = ? AND provider = ?",
                (host_id, provider),
            )
            row = cur.fetchone()
            if row is None:
                # First failure of a new streak. ``last_failure_ts``
                # equals ``first_failure_ts`` for a single-tick streak;
                # subsequent failures advance ``last_failure_ts`` only
                # so the drawer can render "last error N seconds ago"
                # without losing the streak start.
                c.execute(
                    "INSERT INTO host_failure_state "
                    "(host_id, provider, first_failure_ts, last_failure_ts, "
                    "consecutive_failures, paused, paused_at, last_error) "
                    "VALUES (?, ?, ?, ?, 1, 0, NULL, ?)",
                    (host_id, provider, now, now, err_short),
                )
                return
            first_ts, fails, paused = row[0], row[1], bool(row[2])
            new_fails = fails + 1
            if round_threshold is not None and round_threshold > 0:
                should_pause = (not paused) and (new_fails >= round_threshold)
            elif round_threshold == 0:
                # 0 = auto-pause disabled (operator opted out via
                # tuning_snmp_failure_pause_rounds=0). Just record.
                should_pause = False
            else:
                should_pause = (not paused) and (now - first_ts >= window)
            if should_pause:
                c.execute(
                    "UPDATE host_failure_state SET consecutive_failures = ?, "
                    "paused = 1, paused_at = ?, last_failure_ts = ?, "
                    "last_error = ? WHERE host_id = ? AND provider = ?",
                    (new_fails, now, now, err_short, host_id, provider),
                )
                paused_minutes = max(1, int((now - first_ts) // 60))
                # Append-only transition log — captures the EDGE
                # (paused → unpaused → paused → ...) so the timeline
                # endpoint can render the true history rather than
                # synthesising events from the current snapshot. Best-
                # effort error swallow: a transition-log failure must
                # not block the pause itself.
                try:
                    c.execute(
                        "INSERT INTO host_failure_events "
                        "(ts, host_id, provider, kind, error, actor) "
                        "VALUES (?, ?, ?, 'paused', ?, 'sampler')",
                        (now, bare_host, provider or "", err_short),
                    )
                # noinspection PyBroadException
                except Exception as ev_err: # noqa: BLE001
                    print(f"[host_metrics_sampler] {log_label!r} "
                          f"failure-event log write failed: {ev_err}")
                print(f"[host_metrics_sampler] {log_label!r} AUTO-PAUSED after "
                      f"{int(now - first_ts)}s of consecutive failures "
                      f"({new_fails} attempts) — operator must POST "
                      f"/api/hosts/{bare_host}"
                      f"{('/provider/' + provider + '/resume') if provider else '/resume-sampling'}"
                      f" to resume")
                # fire-and-forget Apprise notification on the
                # pause transition. ``asyncio.create_task`` is the
                # supported API since 3.7; it requires a running loop
                # in scope which we're guaranteed inside this async
                # function. Best-effort: a notify failure logs and
                # moves on — the pause itself is the load-bearing
                # side effect.
                #
                # emit-once-with-one-retry. If the first
                # `notify()` raises (Apprise URL down, network blip,
                # apprise-api 5xx), wait 60s and try again ONCE. We
                # deliberately don't loop forever (would spam if
                # Apprise stays down) and we deliberately don't
                # persist the retry across process restarts (the
                # pause itself is durable in `host_failure_state`;
                # the notification is best-effort cake on top).
                try:
                    from logic.ops import notify_with_retry as _notify_with_retry
                    # Resolve the host's operator-facing target (FQDN /
                    # IP) so the notification surfaces what was actually
                    # being probed. The curated `id` is often a short
                    # alias (e.g. `wdmycloud`) while the resolved
                    # target is the real reachable address (e.g.
                    # `wdmycloud.example.com`) — both are needed at
                    # a glance in the email title + body. Resolution
                    # chain mirrors `_resolve_ping_target` /
                    # ping_sampler / SNMP / SSH per the canonical
                    # contract: address → ssh.fqdn → ssh.host → id.
                    target_fqdn = ""
                    try:
                        for _h in iter_curated_hosts():
                            if (_h.get("id") or "").strip() != bare_host:
                                continue
                            _ssh = as_dict(_h.get("ssh"))
                            target_fqdn = (
                                (_h.get("address") or "").strip()
                                or (_ssh.get("fqdn") or "").strip()
                                or (_ssh.get("host") or "").strip()
                                or ""
                            )
                            break
                    except (sqlite3.Error, OSError, RuntimeError,
                            AttributeError, KeyError, TypeError, ValueError):
                        # Curated-host lookup failure is non-fatal — we
                        # fall back to the bare id only in the title.
                        target_fqdn = ""
                    # Title surfaces the bare id (operator-recognisable
                    # short name) PLUS the resolved target in parens
                    # when distinct — gives both the alias and the
                    # actual probed address at a glance.
                    title_target = (
                        f"{bare_host} ({target_fqdn})"
                        if target_fqdn and target_fqdn != bare_host
                        else bare_host
                    )
                    if provider:
                        title = f"⚠️ Provider paused: {title_target} ({provider})"
                        body = (
                            f"{provider} probes for {title_target} have failed "
                            f"{new_fails} consecutive rounds (~{paused_minutes} min). "
                            f"Last error: {err_short or '—'}. "
                            f"Resume manually from the {provider} chip in the host drawer."
                        )
                    else:
                        title = f"⚠️ Host sampling paused: {title_target}"
                        body = (
                            f"{title_target} has been unreachable for {paused_minutes} min "
                            f"after {new_fails} consecutive probe failures. "
                            f"Last error: {err_short or '—'}. "
                            f"Resume manually from the host drawer's banner."
                        )
                    # uses the shared retry helper in
                    # logic.ops so login-event / scheduler / anomaly-watcher
                    # paths get the same semantics. `label` distinguishes
                    # this chain in Admin → Logs.
                    # Lazy main import — avoids circular dependency at module
                    # load. Routes through `spawn_background_task` so the
                    # strong-ref + done-callback contract (see the project conventions
                    # "Background-task lifecycle") is honoured.
                    import main as _main
                    _main.spawn_background_task(
                        _notify_with_retry(
                            title, body, "error",
                            event="host_paused",
                            target_kind="host", target_id=str(bare_host),
                            metadata={
                                "provider": provider or "",
                                "consecutive_failures": int(new_fails),
                                "paused_minutes": int(paused_minutes),
                            },
                            label=f"host_metrics_sampler {host_id!r}",
                        ),
                        label=f"host_paused_notify {host_id!r}",
                    )
                # noinspection PyBroadException
                except Exception as e: # noqa: BLE001
                    print(f"[host_metrics_sampler] {host_id!r} notify dispatch failed: {e}")
                # SSE — paused transition. SPA reacts by re-fetching the
                # one host (banner appears in the drawer immediately
                # instead of waiting for the next 15s host poll). Always
                # publish the BARE host_id so `refreshHostRow(id)` works;
                # surface the provider name as a separate optional field.
                try:
                    from logic import events as _events
                    payload = {
                        "host_id": bare_host,
                        "paused": True,
                        "consecutive_failures": new_fails,
                        "last_error": err_short,
                    }
                    if provider:
                        payload["provider"] = provider
                    _events.publish("host:failure_state_changed", payload)
                # noinspection PyBroadException
                except Exception as ee: # noqa: BLE001
                    print(f"[events] host:failure_state_changed publish failed: {ee}")
            else:
                c.execute(
                    "UPDATE host_failure_state SET consecutive_failures = ?, "
                    "last_failure_ts = ?, last_error = ? "
                    "WHERE host_id = ? AND provider = ?",
                    (new_fails, now, err_short, host_id, provider),
                )
    # noinspection PyBroadException
    except Exception as e: # noqa: BLE001
        print(f"[host_metrics_sampler] {_log_label_with_target(bare_host, provider)!r} failure-state write error: {e}")


# Per-host record_provider_outcome opens its own db_conn per call.
# An earlier attempt at a shared-conn context manager
# (`use_shared_failure_state_conn`) was REVERTED — it bypassed
# db_conn's commit path AND caused "Cannot operate on a closed
# database" errors when notify_with_retry's background task (spawned
# from inside _record_failure's auto-pause branch) outlived the
# outer context. Per-call connection opens are correct + safe; the
# fleet-wide-outage perf optimisation is a real but minor concern
# that does NOT warrant the implementation complexity. If a future
# session wants to batch per-host writes during gather, the safe
# shape is "collect outcomes in-memory, flush via executemany at the
# end of the tick" — NOT a shared connection across concurrent
# probes + background tasks.
async def record_provider_outcome(
    host_id: str, provider: str, ok: bool,
    *, error: str = "", round_threshold: int = 0,
) -> None:
    """Unified outcome recorder — accepts BOTH per-(provider, host) AND
    whole-host shapes.

    * ``provider`` non-empty → per-(provider, host) write keyed
      ``<provider>:<host_id>``; on success ALSO upserts
      ``host_provider_last_ok`` (chip "Updated Xm ago" subtitle).
    * ``provider == ""`` → whole-host write keyed by bare ``host_id``
      (legacy whole-host sampler path); SKIPS the last_ok upsert (no
      per-provider chip subtitle to drive) and SKIPS the defensive
      guard (the guard only applies to per-(provider, host) rows).

    The unified entry point replaces the pre-fix split where the
    whole-host path called bare ``_record_failure`` / ``_clear_failure``
    and the per-(provider, host) path went through this wrapper. Now
    BOTH share ONE state-machine implementation; the legacy bare
    helpers stay as the internal implementation detail this wrapper
    delegates to (still marked ``# audit: bare-failure-ok`` since
    `scripts/audit_record_outcome.py` checks for external bypass).

    Use at every probe boundary that wants the auto-pause + manual-
    resume contract:

        ok = bool(stats and not stats.get("exporter_error"))
        await record_provider_outcome(
            h["id"], "node_exporter", ok,
            error="" if ok else (stats.get("exporter_error") or "no response"),
            round_threshold=tuning.tuning_int(Tunable.NODE_EXPORTER_FAILURE_PAUSE_ROUNDS),
        )

    On success: clears the failure-state row (keyed by provider when
    non-empty, by bare host_id otherwise) AND — only for the per-
    provider shape — upserts ``host_provider_last_ok`` so the SPA can
    render "Updated Xm ago" on the chip.
    On failure: increments the consecutive-failure counter; flips the
    ``paused`` flag when ``round_threshold > 0`` and the count reaches
    threshold. ``round_threshold == 0`` disables auto-pause (only
    records the failure for diagnostic surface). Failure does NOT
    touch last_ok_ts — the operator wants to see when the LAST good
    probe was, not when the most recent attempt happened.

    Empty / falsy ``host_id`` is a no-op so callers don't need
    defensive guards.

    Defence-in-depth (per-provider shape only): when ``ok=False``,
    refuse to record the failure if the host's curated row doesn't
    have ``provider`` configured (the detection in
    ``_host_provider_config``). Catches the operator-reported pattern
    where a stale per-provider field OR a probe-side gate regression
    would otherwise accumulate failures + fire a pause notification
    for a provider the operator says they "don't have set up". On
    first refusal we ALSO delete any pre-existing failure-state row
    for the (host, provider) pair so the orphan self-heals without
    waiting for the next save / redeploy. The whole-host shape
    (provider="") skips this guard because the bare-key row IS the
    whole-host marker — there's no curated-config concept of
    "configured for whole-host probing".
    """
    if not host_id:
        return
    log_label = f"{provider}:{host_id}" if provider else host_id
    if not ok and provider:
        # Defensive guard — see docstring. Skip recording AND wipe any
        # leftover paused row from a pre-fix accumulation. Only known
        # provider names get the check; unknown provider passes through
        # so a future provider added without TUNABLE wiring can still
        # record failures during initial bring-up. Whole-host shape
        # (provider="") never reaches here.
        if provider in _PROVIDER_PREFIXES:
            cfg = _host_provider_config()
            # Empty cfg dict means hosts_config read failed — fall back
            # to "trust the caller's gate" to avoid silently disabling
            # failure recording for a real outage during a DB blip.
            if cfg and host_id in cfg and provider not in cfg[host_id]:
                deleted = 0
                try:
                    with db_conn() as c:
                        cur = c.execute(
                            "DELETE FROM host_failure_state "
                            "WHERE host_id = ? AND provider = ?",
                            (host_id, provider),
                        )
                        deleted = cur.rowcount or 0
                        cur = c.execute(
                            "DELETE FROM host_provider_last_ok "
                            "WHERE host_id = ? AND provider = ?",
                            (host_id, provider),
                        )
                        deleted += cur.rowcount or 0
                except (sqlite3.Error, RuntimeError, OSError) as e:
                    print(f"[host_metrics_sampler] {log_label} orphan cleanup failed: {e}")
                if deleted:
                    print(
                        f"[host_metrics_sampler] {log_label} record refused: "
                        f"host has no {provider} mapping in curated config "
                        f"(cleaned {deleted} orphan row(s))"
                    )
                else:
                    print(
                        f"[host_metrics_sampler] {log_label} record refused: "
                        f"host has no {provider} mapping in curated config"
                    )
                return
    if ok:
        _clear_failure(host_id, provider=provider)  # audit: bare-failure-ok
        # Stamp the last-ok timestamp. Best-effort — a DB blip here
        # doesn't break the probe flow. Whole-host shape (provider="")
        # has no per-(provider, host) chip subtitle to drive — skip
        # the upsert so the whole-host write stays one-table.
        if provider:
            try:
                now_ts = int(time.time())
                with db_conn() as c:
                    c.execute(
                        "INSERT OR REPLACE INTO host_provider_last_ok "
                        "(host_id, provider, last_ok_ts) VALUES (?, ?, ?)",
                        (host_id, provider, now_ts),
                    )
            # noinspection PyBroadException
            except Exception as e: # noqa: BLE001
                print(f"[host_metrics_sampler] {_log_label_with_target(host_id, provider)} last_ok upsert failed: {e}")
    else:
        await _record_failure(  # audit: bare-failure-ok
            host_id, time.time(), error,
            round_threshold=round_threshold,
            provider=provider,
        )


def _clear_failure(
    host_id: str, *,
    actor: str = "sampler",
    provider: str = "",
) -> None:
    """Clear the failure tracking row on a successful probe. No-op
    when there's no row to clear (the common case).

    ``provider=''`` clears the whole-host row; ``provider='<name>'``
    clears the per-(provider, host) row. ``actor`` distinguishes
    auto-recovery ("sampler" — default) from manual resume
    ("admin:<user>" — passed by the API endpoints).
    """
    had_row = False
    log_label = f"{provider}:{host_id}" if provider else host_id
    try:
        with db_conn() as c:
            # Snapshot whether the row was paused BEFORE the delete so
            # we know whether to emit a 'recovered' transition event.
            # Recovery from a non-paused failure streak (the streak
            # fizzled before reaching the threshold) doesn't deserve
            # an event — that's normal sampler noise and would flood
            # the timeline.
            row = c.execute(
                "SELECT paused FROM host_failure_state "
                "WHERE host_id = ? AND provider = ?",
                (host_id, provider),
            ).fetchone()
            paused_was = bool(row[0]) if row else False
            cur = c.execute(
                "DELETE FROM host_failure_state "
                "WHERE host_id = ? AND provider = ?",
                (host_id, provider),
            )
            had_row = (cur.rowcount or 0) > 0
            if had_row and paused_was:
                # Append-only transition log — captures the
                # paused → recovered EDGE.
                try:
                    c.execute(
                        "INSERT INTO host_failure_events "
                        "(ts, host_id, provider, kind, error, actor) "
                        "VALUES (?, ?, ?, 'recovered', NULL, ?)",
                        (time.time(), host_id, provider or "", actor),
                    )
                # noinspection PyBroadException
                except Exception as ev_err: # noqa: BLE001
                    print(f"[host_metrics_sampler] {log_label!r} "
                          f"recovery-event log write failed: {ev_err}")
    # noinspection PyBroadException
    except Exception as e: # noqa: BLE001
        print(f"[host_metrics_sampler] {_log_label_with_target(host_id, provider)!r} failure-state clear error: {e}")
        return
    if had_row:
        # SSE — only publish when something actually cleared. Most ticks
        # are no-ops (no failure row in the first place) so this avoids
        # one event per host per tick on a healthy fleet. Always emit
        # the BARE host_id so the SPA's `refreshHostRow(id)` works;
        # surface provider as a separate field.
        try:
            from logic import events as _events
            payload = {"host_id": host_id, "paused": False, "cleared": True}
            if provider:
                payload["provider"] = provider
            _events.publish("host:failure_state_changed", payload)
        # noinspection PyBroadException
        except Exception as e: # noqa: BLE001
            print(f"[events] host:failure_state_changed clear publish failed: {e}")


async def _probe_one(
    client: httpx.AsyncClient,
    host: dict,
    sem: asyncio.Semaphore,
) -> Optional[dict]:
    """Probe NE for one host; return a row dict if there's anything
    worth storing, else None. Per-host failures isolated — one dead
    exporter doesn't cascade to the rest of the fleet. Honours the
    permanent-fail pause flag: paused hosts skip the probe entirely
    (no network attempt, no log spam).

    Write batching contract: the INSERT used to happen inline here.
    Now the caller (the NE tick body in `host_metrics_sampler_loop`)
    collects every non-None return into a single
    ``executemany`` flushed via :func:`_persist_ne_tick`, so a 50-host
    fleet does ONE connection-open per NE tick instead of 50. The
    success-path side-effects (failure-state clear,
    ``record_provider_outcome`` upsert, SSE ``host:history_appended``
    publish, the per-host wrote-blurb log line) are deferred to
    :func:`_fire_ne_success_side_effects`, called only after the
    batched persist succeeds — so a bulk-write failure leaves
    every host in its pre-tick failure-state rather than leaking
    half-success markers.
    """
    async with sem:
        hid = host["id"]
        ne_url = host["ne_url"]
        # Permanent-fail short-circuit. Paused hosts skip the
        # probe entirely until the operator resumes via the API.
        state = _get_failure_state(hid)
        if state and state["paused"]:
            return None
        # DNS-failure short-circuit — when the boot DNS check (or a
        # previous probe attempt) caught the ne_url's hostname as
        # unresolvable, skip the probe entirely for the cached TTL.
        # Eliminates the per-tick executor-thrash on a fleet with
        # unresolvable hostnames. Latches off on the next successful
        # DNS resolution so a recovered DNS server brings the host
        # back without operator intervention.
        from urllib.parse import urlparse as _urlparse
        from logic.dns_skip import should_skip_dns as _should_skip_dns
        _parsed = _urlparse(ne_url)
        if _parsed.hostname and _should_skip_dns(_parsed.hostname):
            return None
        now = time.time()
        # Per-use read so Admin → Config edits to the NE probe timeout
        # land on the next sampler tick. Defensive fallback to the
        # legacy hardcoded 10s if `tuning_int` raises (corrupt DB).
        try:
            _ne_to = float(tuning.tuning_int(
                Tunable.NODE_EXPORTER_PROBE_TIMEOUT_SECONDS))
        except (KeyError, ValueError, TypeError):
            _ne_to = 10.0
        try:
            stats = await _ne.probe_node(client, ne_url, timeout=_ne_to)
        # noinspection PyBroadException
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as e: # noqa: BLE001
            print(f"[host_metrics_sampler] {hid!r} target={ne_url} probe error: {e}")
            # Unified outcome — TWO writes via ONE state machine: the
            # whole-host bare-key row (provider="") + the per-(provider,
            # host) prefixed row. Pre-fix this was a bare `_record_failure`
            # for the whole-host path PLUS a separate `record_provider_outcome`
            # for the per-provider path. Now both shapes route through
            # `record_provider_outcome` so any future state-machine fix
            # (auto-pause threshold change, SSE event emission, etc.)
            # lands once instead of risking divergence.
            await record_provider_outcome(hid, "", False, error=str(e))
            try:
                await record_provider_outcome(
                    hid, "node_exporter", False,
                    error=str(e),
                    round_threshold=tuning.tuning_int(
                        Tunable.NODE_EXPORTER_FAILURE_PAUSE_ROUNDS),
                )
            except (KeyError, ValueError, TypeError):
                pass
            return None
        if stats.get("exporter_error"):
            err = stats["exporter_error"]
            # Include the resolved ne_url in the log so the operator
            # can verify the sampler is probing the RIGHT address
            # (the curated `id` is often a short alias; the URL is
            # what actually gets hit).
            print(f"[host_metrics_sampler] {hid!r} target={ne_url} "
                  f"exporter_error: {err}")
            await record_provider_outcome(hid, "", False, error=str(err))
            try:
                await record_provider_outcome(
                    hid, "node_exporter", False,
                    error=str(err),
                    round_threshold=tuning.tuning_int(
                        Tunable.NODE_EXPORTER_FAILURE_PAUSE_ROUNDS),
                )
            except (KeyError, ValueError, TypeError):
                pass
            return None

        prev = _last_counters.get(hid)
        row, next_counter = _compute_row(hid, now, stats, prev)
        if next_counter is not None:
            _last_counters[hid] = next_counter
        if row is None:
            if prev is None and next_counter is not None:
                print(f"[host_metrics_sampler] {hid!r} target={ne_url} "
                      f"first sample established counter baseline; "
                      f"no row to write yet")
            else:
                print(f"[host_metrics_sampler] {hid!r} target={ne_url} "
                      f"probe returned no meaningful metrics; skipping INSERT")
            return None
        # Stash the resolved URL on the row dict so the deferred
        # success-side-effects + wrote-blurb log line have it without
        # re-resolving. Underscore-prefixed so the INSERT-row builder
        # in `_persist_ne_tick` skips it.
        row["_ne_url"] = ne_url
        return row


async def _persist_ne_tick(rows: list[dict]) -> None:
    """Bulk INSERT for the NE leg of one tick. Replaces the per-host
    `with db_conn() as c: c.execute(...)` inside :func:`_probe_one`
    -- on a 50-host fleet this collapses 50 connection-opens (each
    waiting on PRAGMA busy_timeout for the writer lock) into ONE.
    Mirrors the Beszel / Pulse / Webmin ``_persist_tick`` shape.

    On success, fires the deferred per-host side-effects (failure
    state clear, last_ok upsert, SSE publish, wrote-blurb log). On
    failure, leaves every host's failure state untouched -- the
    next tick will overwrite cleanly when contention clears.
    """
    if not rows:
        return
    try:
        payload = [
            (
                r["ts"], r["host_id"], r["cpu_percent"],
                r["mem_used"], r["mem_total"],
                r["disk_used"], r["disk_total"],
                r["net_rx_bps"], r["net_tx_bps"],
                r["disk_read_bps"], r["disk_write_bps"],
            )
            for r in rows
        ]
        with db_conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO host_metrics_samples "
                "(ts, host_id, cpu_percent, mem_used, mem_total, "
                "disk_used, disk_total, net_rx_bps, net_tx_bps, "
                "disk_read_bps, disk_write_bps) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                payload,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[host_metrics_sampler] NE bulk insert failed for "
              f"{len(rows)} rows: {e}")
        return
    # Bulk write succeeded — fire per-host success side-effects.
    for r in rows:
        await _fire_ne_success_side_effects(r)


async def _fire_ne_success_side_effects(row: dict) -> None:
    """Per-host bookkeeping deferred until the batched INSERT in
    :func:`_persist_ne_tick` succeeds. Failure-state clear +
    per-provider last_ok upsert + SSE history_appended publish +
    wrote-blurb log line. Each side-effect wrapped in try/except so
    a bus regression / DB blip on one host doesn't block the rest."""
    hid = row["host_id"]
    ne_url = row.get("_ne_url", "")
    # Whole-host bare-key + per-(provider, host) prefixed row -- same
    # pre-batch contract; both routed through record_provider_outcome
    # so the state machine stays single-source-of-truth.
    await record_provider_outcome(hid, "", True)
    try:
        await record_provider_outcome(hid, "node_exporter", True)
    except (KeyError, ValueError, TypeError):
        pass
    try:
        from logic import events as _events
        _events.publish("host:history_appended", {
            "host_id": hid,
            "ts": row["ts"],
        })
    # noinspection PyBroadException
    except Exception as e:  # noqa: BLE001
        print(f"[host_metrics_sampler] {hid!r} target={ne_url} history_appended publish failed: {e}")
    net_blurb = (
        f"net rx={row['net_rx_bps']:.0f} tx={row['net_tx_bps']:.0f} B/s"
        if (row["net_rx_bps"] is not None and row["net_tx_bps"] is not None)
        else "net=skip"
    )
    disk_blurb = (
        f"diskio r={row['disk_read_bps']:.0f} w={row['disk_write_bps']:.0f} B/s"
        if (row["disk_read_bps"] is not None and row["disk_write_bps"] is not None)
        else "diskio=skip"
    )
    # Include the resolved ne_url so operators can verify WHICH
    # exporter URL the sample came from — the curated `id` is often
    # a short alias and a misconfigured `ne_url` would silently pull
    # data from a different host. Matches the convention surfaced by
    # host_net_sampler / ping_sampler / SNMP for every per-host
    # probe log line.
    print(f"[host_metrics_sampler] {hid!r} target={ne_url} "
          f"wrote cpu={row['cpu_percent']} "
          f"mem={row['mem_used']}/{row['mem_total']} "
          f"disk={row['disk_used']}/{row['disk_total']} {net_blurb} {disk_blurb}")


async def _probe_one_snmp(host: dict, sem: asyncio.Semaphore) -> None:
    """Probe one SNMP host on the sampler tick to record per-host
    failure state for the auto-pause behaviour, mirroring the NE pattern.

    Failure tracking uses the per-(provider, host) row in
    ``host_failure_state`` (composite PK ``(host_id='snmp:web01' ...)``
    pre-migration, ``(host_id='web01', provider='snmp')`` post-migration 2).
    The SNMP failure streak stays independent of the NE / Webmin streaks
    on the same host.

    Phase-1 caveat: the per-(provider, host) pause flag isn't surfaced
    in the drawer banner yet (banner reads the whole-host row). Operators
    see the auto-pause via the `[host_metrics_sampler] snmp:<host>
    AUTO-PAUSED` log line; UI surface comes in a follow-up.
    """
    async with sem:
        from logic import snmp as _snmp
        if not _snmp.has_snmp_support():
            return
        hid = host["id"]
        snmp_key = f"snmp:{hid}"  # log label only — DB lookups use composite PK
        # Permanent-fail short-circuit on the (host, snmp) row.
        state = _get_failure_state(hid, "snmp")
        if state and state["paused"]:
            return
        # Resolve target via the SAME chain `_merge_one_host` uses.
        try:
            import json as _json
            aliases_raw = _get_setting(Settings.SNMP_ALIASES, "{}") or "{}"
            aliases = _json.loads(aliases_raw)
            if not isinstance(aliases, dict):
                aliases = {}
        except ValueError:
            aliases = {}
        # Resolution chain MUST mirror the on-demand path
        # (`api_hosts_port_scan` for port scan, `_merge_one_host` for
        # the SNMP-via-merge path): aliases → snmp_name → address →
        # SKIP. Pre-fix the sampler stopped at `snmp_name` and never
        # consulted the curated `address` field, so hosts that
        # cleared `snmp_name` intending to inherit from the shared
        # `address` (the documented address-fallback contract) silently
        # stopped being probed by the sampler — `_probe_one_snmp`
        # returned early on `if not snmp_target` and the per-tick
        # `host_snmp_samples` row never landed. Drawer charts went
        # flat at the moment the operator cleared `snmp_name` and
        # stayed flat forever (until manually reverted). NE hosts
        # were unaffected because `_probe_one` reads `ne_url`
        # directly. Fix: fall through to `address` when both alias
        # and snmp_name are empty.
        snmp_target = (
            aliases.get(hid)
            or (host.get("snmp_name") or "").strip()
            or (host.get("address") or "").strip()
            or ""
        )
        if not snmp_target:
            return
        _snmp_raw = host.get("snmp")
        snmp_cfg: dict = _snmp_raw if isinstance(_snmp_raw, dict) else {}
        community = (snmp_cfg.get("community") or "").strip() \
                    or (_get_setting(Settings.SNMP_DEFAULT_COMMUNITY) or "public")
        version = ((snmp_cfg.get("version") or "").strip().lower()
                   or (_get_setting(Settings.SNMP_DEFAULT_VERSION) or "v2c"))
        try:
            port = int(snmp_cfg.get("port") or tuning.tuning_int(Tunable.SNMP_DEFAULT_PORT))
        except (TypeError, ValueError):
            port = 161
        v3_user = ((snmp_cfg.get("v3_user") or "").strip()
                   or _get_setting(Settings.SNMP_V3_USER) or "")
        v3_auth = ((snmp_cfg.get("v3_auth_key") or "").strip()
                   or _get_setting(Settings.SNMP_V3_AUTH_KEY) or "")
        v3_priv = ((snmp_cfg.get("v3_priv_key") or "").strip()
                   or _get_setting(Settings.SNMP_V3_PRIV_KEY) or "")
        snmp_timeout = float(tuning.tuning_int(Tunable.SNMP_PROBE_TIMEOUT_SECONDS))
        # Per-host walk_concurrency override (Dell iDRAC / Cisco IMC /
        # Supermicro IPMI need > 1 to fit pysnmp v7's per-walk overhead
        # inside the probe budget; safety-floor concurrency=1 stays the
        # default for low-power embedded snmpd's).
        _raw_walk_conc = snmp_cfg.get("walk_concurrency")
        row_walk_conc: Optional[int] = (
            _safe_int(_raw_walk_conc) if _raw_walk_conc else None
        )
        # Per-host vendor MIB selector — auto-detect from sysDescr when
        # None, otherwise the operator-declared subset (dell / cisco /
        # apc / ucd / synology / printer).
        row_vendors_raw = snmp_cfg.get("vendors")
        row_vendors = (
            set(row_vendors_raw)
            if isinstance(row_vendors_raw, list) and row_vendors_raw
            else None
        )
        now = time.time()
        # Round-count auto-pause threshold. 0 = disabled
        # (operator opted out). Per-tick read so an Admin → Config save
        # takes effect on the next tick without restart.
        snmp_pause_rounds = tuning.tuning_int(Tunable.SNMP_FAILURE_PAUSE_ROUNDS)
        try:
            r = await _snmp.probe_snmp(
                snmp_target,
                community=community, version=version, port=port,
                v3_user=v3_user, v3_auth_key=v3_auth, v3_priv_key=v3_priv,
                timeout=snmp_timeout,
                walk_concurrency=row_walk_conc,
                vendors=row_vendors,
            )
        # noinspection PyBroadException
        except Exception as e:  # noqa: BLE001
            print(f"[host_metrics_sampler] {snmp_key} target={snmp_target} probe error: {e}")
            await record_provider_outcome(
                hid, "snmp", False,
                error=str(e), round_threshold=snmp_pause_rounds,
            )
            return
        if r.get("error") and not r.get("hosts"):
            err = r["error"]
            # Cool-down short-circuit isn't a real failure event —
            # the probe was SKIPPED, not attempted. Don't count it
            # toward the auto-pause threshold; just log + return.
            # Real probe failures (timeout, network error, agent
            # rejection) DO count. Structured-skip detection: prefer
            # `r.get("skipped_cooldown")` when the probe wires it;
            # fall back to substring for legacy.
            if r.get("skipped_cooldown") or (isinstance(err, str) and "cool-down" in err):
                print(f"[host_metrics_sampler] {snmp_key} target={snmp_target} cool-down skip: {err}")
                return
            print(f"[host_metrics_sampler] {snmp_key} target={snmp_target} snmp error: {err}")
            await record_provider_outcome(
                hid, "snmp", False,
                error=str(err), round_threshold=snmp_pause_rounds,
            )
            return
        # Success — route through `record_provider_outcome` so the
        # `host_provider_last_ok` UPSERT lands (chip "Updated Xm ago"
        # subtitle depends on it). Bare `_clear_failure(snmp_key)`
        # would clear the failure row but skip the last_ok stamp,
        # leaving the chip subtitle invisible forever.
        await record_provider_outcome(hid, "snmp", True)
        # write a sample into host_snmp_samples for the
        # time-series chart cards (CPU per-core, load avg, memory
        # stacked-area). Skip-don't-synthesize: when mem_total didn't
        # come back, we don't insert a row. Storing zeros would mask
        # exactly the "host stopped responding" signal the chart should
        # surface.
        try:
            _stats_raw = next(iter((r.get("hosts") or {}).values()), None)
            # Narrow to a concrete dict so the cascade of `.get(...)`
            # calls below stays pyright-clean. `if stats:` doesn't
            # narrow tightly enough — pyright still sees the variable as
            # `Optional[Any]` and flags every attribute access.
            stats: dict = _stats_raw if isinstance(_stats_raw, dict) else {}
            if stats:
                mem_total = int(stats.get("host_mem_total") or 0)
                # switches / routers that don't expose hrStorage
                # still report IF-MIB counters; insert when EITHER
                # mem_total OR net totals are present, so the throughput
                # chart populates on devices that don't surface memory.
                rx_raw_present = stats.get("host_net_rx_total_bytes") is not None
                tx_raw_present = stats.get("host_net_tx_total_bytes") is not None
                # printer hosts may report ONLY prtMarkerLifeCount
                # (no mem, no IF-MIB). Insert the row so the sparkline
                # has data to plot.
                page_count_present = stats.get("printer_page_count") is not None
                # APC UPS hosts report load / battery percentages but
                # may have neither hrStorage nor IF-MIB on basic models
                # . Without this branch, UPS history rows never
                # got inserted so the Output Load chart had no data
                # to plot. host_load_percent extracted from PowerNet
                # OID 1.3.6.1.4.1.318.1.1.1.4.2.3.0 in `logic/snmp.py`.
                load_pct_present = stats.get("host_load_percent") is not None
                batt_pct_present = stats.get("host_battery_percent") is not None
                batt_temp_present = stats.get("host_battery_temp_c") is not None
                # Power-quality scalars also count as UPS presence, so a UPS
                # firmware that exposes voltage / frequency but not battery %
                # still persists a row (the Apps APC card reads from the DB).
                ups_pq_present = any(
                    stats.get(k) is not None for k in (
                        "host_ups_input_voltage", "host_ups_output_voltage",
                        "host_ups_input_freq_hz", "host_ups_battery_replace"))
                ups_present = (load_pct_present or batt_pct_present
                               or batt_temp_present or ups_pq_present)
                if (mem_total > 0 or rx_raw_present or tx_raw_present
                    or page_count_present or ups_present):
                    cores = stats.get("host_cpu_per_core") or []
                    cpu_used_pct = _float_or_none(stats.get("host_cpu_percent"))
                    # uptime in seconds (NULL when sysUpTime
                    # didn't come back; lets the drawer detect reboots
                    # by comparing adjacent rows).
                    uptime_s = _int_or_none(stats.get("host_uptime_s"))
                    # total net counters (IF-MIB ifHCInOctets /
                    # ifHCOutOctets sums). NULL when neither came back so
                    # the chart layer can tell "host idle" from "host
                    # stopped responding."
                    rx_total = _int_or_none(stats.get("host_net_rx_total_bytes"))
                    tx_total = _int_or_none(stats.get("host_net_tx_total_bytes"))
                    # printer lifetime page count. NULL for
                    # non-printer SNMP hosts.
                    page_count = _int_or_none(stats.get("printer_page_count"))
                    # APC UPS time-series fields. NULL when the
                    # host isn't a UPS or the OIDs didn't come back.
                    ups_load_pct = _float_or_none(stats.get("host_load_percent"))
                    ups_batt_pct = _float_or_none(stats.get("host_battery_percent"))
                    ups_batt_temp = _float_or_none(stats.get("host_battery_temp_c"))
                    # APC UPS label strings + runtime so the Apps APC card
                    # renders its full panel straight from the sample row
                    # (never a live host probe). NULL for non-UPS hosts.
                    ups_status_lbl = stats.get("host_ups_status") or None
                    ups_batt_status_lbl = stats.get("host_battery_status") or None
                    ups_runtime_s = _int_or_none(stats.get("host_battery_runtime_s"))
                    # APC power-quality scalars (input/output voltage,
                    # mains frequency, last-transfer cause, battery-replace
                    # flag, self-test result). NULL for non-UPS hosts.
                    ups_in_v = _float_or_none(stats.get("host_ups_input_voltage"))
                    ups_out_v = _float_or_none(stats.get("host_ups_output_voltage"))
                    ups_in_hz = _float_or_none(stats.get("host_ups_input_freq_hz"))
                    ups_last_xfer = stats.get("host_ups_last_transfer") or None
                    ups_batt_replace = _int_or_none(stats.get("host_ups_battery_replace"))
                    ups_self_test = stats.get("host_ups_self_test") or None
                    # Aggregate disk totals — capture so SNMP-only
                    # hosts can render the inline disk sparkline. The
                    # extractor's `host_disk_total` / `host_disk_used`
                    # already respect the per-host exclude_mounts
                    # list (dd-wrt's phantom `/opt` is filtered
                    # before reaching this dict). Stored as bytes;
                    # disk_percent is derived in the API layer to
                    # avoid fixed-precision drift across rows.
                    disk_total_b = _int_or_none(stats.get("host_disk_total"))
                    disk_used_b = _int_or_none(stats.get("host_disk_used"))
                    with db_conn() as c:
                        c.execute(
                            "INSERT OR REPLACE INTO host_snmp_samples "
                            "(ts, host_id, cpu_per_core, cpu_used_pct, "
                            "load_1m, load_5m, load_15m, "
                            "mem_total, mem_used, mem_buffers, mem_cached, mem_free, "
                            "uptime_s, net_rx_total_bytes, net_tx_total_bytes, "
                            "printer_page_count, load_percent, battery_percent, "
                            "battery_temp_c, disk_total, disk_used, "
                            "ups_status, battery_status, battery_runtime_s, "
                            "ups_input_voltage, ups_output_voltage, "
                            "ups_input_freq_hz, ups_last_transfer, "
                            "ups_battery_replace, ups_self_test) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                            "?, ?, ?, ?, ?, ?)",
                            (
                                int(now), hid,
                                json.dumps(list(cores)) if cores else None,
                                cpu_used_pct,
                                stats.get("host_load_1m"),
                                stats.get("host_load_5m"),
                                stats.get("host_load_15m"),
                                mem_total,
                                int(stats.get("host_mem_used") or 0),
                                int(stats.get("host_mem_buffers") or 0),
                                int(stats.get("host_mem_cached") or 0),
                                int(stats.get("host_mem_free") or 0),
                                uptime_s,
                                rx_total,
                                tx_total,
                                page_count,
                                ups_load_pct,
                                ups_batt_pct,
                                ups_batt_temp,
                                disk_total_b,
                                disk_used_b,
                                ups_status_lbl,
                                ups_batt_status_lbl,
                                ups_runtime_s,
                                ups_in_v,
                                ups_out_v,
                                ups_in_hz,
                                ups_last_xfer,
                                ups_batt_replace,
                                ups_self_test,
                            ),
                        )
                        # per-interface counter snapshot for the
                        # switch / router per-port throughput chart.
                        # Skip pseudo NICs (loopback / docker / veth)
                        # via the same prefix list the totals use; one
                        # row per active iface per tick. Counters are
                        # cumulative — chart layer computes deltas.
                        loopback_prefixes = (
                            "lo", "docker", "veth", "br-", "cni",
                            "flannel", "cali", "vmnet", "tap", "tun",
                            "ovs",
                        )
                        ifaces = stats.get("network_ifaces") or []
                        if ifaces:
                            iface_rows = []
                            for iface in ifaces:
                                name = (iface.get("name") or "").strip()
                                if not name:
                                    continue
                                nlc = name.lower()
                                if any(nlc.startswith(p) for p in loopback_prefixes):
                                    continue
                                rx = iface.get("rx_bytes")
                                tx = iface.get("tx_bytes")
                                if rx is None and tx is None:
                                    continue
                                # slice 4 — IF-MIB ifHighSpeed
                                # (Mbps). NULL on devices that don't
                                # expose v2c HC counters; heatmap
                                # renders such ifaces in grey.
                                speed_raw = iface.get("link_speed_mbps")
                                speed_mbps = (
                                    int(speed_raw) if speed_raw is not None else None
                                )
                                iface_rows.append((
                                    int(now), hid, name,
                                    int(rx) if rx is not None else None,
                                    int(tx) if tx is not None else None,
                                    speed_mbps,
                                ))
                            if iface_rows:
                                c.executemany(
                                    "INSERT OR REPLACE INTO host_snmp_iface_samples "
                                    "(ts, host_id, ifname, in_bytes, out_bytes, link_speed_mbps) "
                                    "VALUES (?, ?, ?, ?, ?, ?)",
                                    iface_rows,
                                )
                        # Per-temperature-probe sample rows for Dell
                        # server hosts. One row per probe
                        # per tick when the SNMP probe extracted any
                        # temperatureProbeTable entries with a valid
                        # celsius reading. Skip-don't-synthesize: rows
                        # whose `celsius` came back None (sentinel /
                        # disconnected probe) are NOT inserted, so a
                        # temporary read failure on probe N doesn't
                        # paper its line over with a flat zero.
                        temps = stats.get("host_dell_temps") or []
                        if temps:
                            temp_rows = []
                            for row in temps:
                                cel = row.get("celsius")
                                if cel is None:
                                    continue
                                idx = str(row.get("idx") or "")
                                if not idx:
                                    continue
                                name = row.get("name") or f"temp-{idx}"
                                temp_rows.append((
                                    int(now), hid, idx, name, float(cel),
                                ))
                            if temp_rows:
                                c.executemany(
                                    "INSERT OR REPLACE INTO host_snmp_temp_samples "
                                    "(ts, host_id, probe_idx, probe_name, value_c) "
                                    "VALUES (?, ?, ?, ?, ?)",
                                    temp_rows,
                                )
        # noinspection PyBroadException
        except Exception as e:  # noqa: BLE001
            print(f"[host_metrics_sampler] {snmp_key} target={snmp_target} snmp_sample insert failed: {e}")


def _prune_old_samples() -> int:
    """Delete host-metrics / SNMP / incident rows older than the retention window;
    returns the total deleted-row count."""
    days = tuning.tuning_int(Tunable.STATS_HISTORY_DAYS)
    cutoff = int(time.time() - days * 86400)
    try:
        # Each table is pruned via the chunked helper (logic.db.
        # prune_rows_older_than) — bounded lock-hold per chunk AND a separate
        # transaction per table, so this 5-table sweep no longer holds the
        # single writer lock across ALL deletes in one transaction (the old
        # shape queued every other writer behind it for the full duration —
        # the contention that surfaced as the db_size prune "taking 2s").
        # Densities differ (host_metrics 1 row/tick, host_snmp denser,
        # host_snmp_iface one row per active iface/tick, host_snmp_temp
        # 4-12 probes/tick on Dell servers) — all share the same retention
        # window.
        removed = 0
        for tbl in ("host_metrics_samples", "host_snmp_samples",
                    "host_snmp_iface_samples", "host_snmp_temp_samples"):
            removed += prune_rows_older_than(tbl, cutoff)
        # host_failure_events — independently tunable retention via
        # ``tuning_incidents_retention_days`` (default 90; 0 disables
        # pruning entirely, restoring the previous "keep every incident
        # forever" behaviour for deployments that want the full audit
        # trail). Default landed at 90 days — a quarter's worth of
        # post-mortem learning material without unbounded growth.
        incidents_days = tuning.tuning_int(Tunable.INCIDENTS_RETENTION_DAYS)
        if incidents_days > 0:
            incidents_cutoff = int(time.time() - incidents_days * 86400)
            removed += prune_rows_older_than("host_failure_events", incidents_cutoff)
        # host_port_scans — one row per OPEN port per scan, written by the
        # on-demand /port-scan endpoint AND the port_scan_refresh schedule kind;
        # it has no sampler of its own, so its retention sweep rides here.
        # Independently tunable via ``tuning_port_scan_retention_days`` (default
        # 90; 0 disables) — the lone otherwise-unbounded table. Seeks the plain
        # idx_host_port_scans_ts index.
        port_scan_days = tuning.tuning_int(Tunable.PORT_SCAN_RETENTION_DAYS)
        if port_scan_days > 0:
            port_scan_cutoff = int(time.time() - port_scan_days * 86400)
            removed += prune_rows_older_than("host_port_scans", port_scan_cutoff)
        return removed
    # noinspection PyBroadException
    except Exception as e: # noqa: BLE001
        print(f"[host_metrics_sampler] prune failed: {e}")
        return 0


def _resolve_outer_interval() -> int:
    """Outer-loop tick interval — the SMALLEST of the per-sampler
    cadences so neither NE nor SNMP gets starved when an operator
    sets one slower than the other. Per-sampler ``*_due`` gates inside
    the loop ensure each sub-probe only fires at its own cadence.
    """
    global_iv = tuning.tuning_int(Tunable.STATS_SAMPLE_INTERVAL_SECONDS)
    ne_iv = tuning.tuning_int(Tunable.NODE_EXPORTER_SAMPLE_INTERVAL_SECONDS)
    snmp_iv = tuning.tuning_int(Tunable.SNMP_SAMPLE_INTERVAL_SECONDS)
    candidates = [global_iv or 300]
    if ne_iv > 0:
        candidates.append(ne_iv)
    if snmp_iv > 0:
        candidates.append(snmp_iv)
    return max(30, min(candidates))


async def host_metrics_sampler_loop() -> None:
    """Lifespan-managed sampler. Outer loop ticks at the MINIMUM of
    every configured per-sampler cadence; per-sampler ``*_due`` gates
    inside the loop fire each sub-probe at its own cadence.

    Cadence resolution per sub-sampler (DB > env > default):
      - node-exporter: ``tuning_node_exporter_sample_interval_seconds``
        when > 0, else ``tuning_stats_sample_interval_seconds``.
      - SNMP: ``tuning_snmp_sample_interval_seconds`` when > 0, else
        ``tuning_stats_sample_interval_seconds``.
    """
    _last_counters.clear()
    # Wait a beat so DB tables exist + hosts_config is loaded before the
    # first probe. Same pattern as host_net_sampler / stats_sampler.
    interval = _resolve_outer_interval()
    await asyncio.sleep(min(60, interval))
    tick = 0
    last_snmp_ts = 0.0
    last_ne_ts = 0.0
    from logic.sampler_metrics import record_tick as _record_tick
    while True:
        # Wall-clock tick body so Stats → Samplers panel surfaces
        # per-tick duration trends. Inlined (vs. lifespan_sampler_loop
        # helper) because this sampler interleaves NE / SNMP / prune
        # on independent cadences inside one tick.
        _tick_t0 = time.perf_counter()
        _tick_ok = True
        _tick_err = ""
        try:
            active = _active_providers()
            now_ts = time.time()
            # NE-aware permanent-fail tracking. Independent of SNMP.
            # NE cadence is independent of the outer loop when
            # `tuning_node_exporter_sample_interval_seconds > 0`.
            # Falls back to the global stats interval when 0 so legacy
            # deployments keep their existing behaviour.
            if "node_exporter" in active:
                ne_interval_cfg = tuning.tuning_int(Tunable.NODE_EXPORTER_SAMPLE_INTERVAL_SECONDS)
                ne_due = (ne_interval_cfg <= 0) or (now_ts - last_ne_ts >= ne_interval_cfg)
                if ne_due:
                    hosts = _load_curated_hosts()
                    if hosts:
                        sem = asyncio.Semaphore(tuning.tuning_int(Tunable.HOST_METRICS_PROBE_CONCURRENCY))
                        # operator-tunable timeout shared with the
                        # other NE consumers in main.py. Pre-fix this was
                        # 15s while the other sites used 10s — drift class.
                        _ne_timeout = tuning.tuning_int(Tunable.NODE_EXPORTER_PROBE_TIMEOUT_SECONDS)
                        async with httpx.AsyncClient(verify=False, timeout=float(_ne_timeout)) as client:
                            # `_probe_one` now RETURNS a row dict (or None) instead of
                            # writing inline; collect + ONE bulk executemany via
                            # `_persist_ne_tick`. On a 50-host fleet this collapses
                            # 50 per-host connection-opens (each waiting on PRAGMA
                            # busy_timeout for the writer lock) into ONE. Matches
                            # the Beszel / Pulse / Webmin `_persist_tick` shape.
                            ne_results = await asyncio.gather(
                                *(_probe_one(client, h, sem) for h in hosts),
                                return_exceptions=True,
                            )
                            ne_rows = [
                                r for r in ne_results
                                if isinstance(r, dict)
                            ]
                            await _persist_ne_tick(ne_rows)
                    last_ne_ts = now_ts
            # SNMP-aware permanent-fail tracking. Independent of
            # the NE block above (a host can have NE off + SNMP on, or
            # both, or neither). Failure-state rows are keyed
            # `snmp:<host_id>` so SNMP / NE streaks stay separate. A
            # paused SNMP host skips the probe entirely on subsequent
            # ticks until the operator clears the row.
            # SNMP cadence is independent of NE when
            # `tuning_snmp_sample_interval_seconds > 0`. Falls back to
            # the global stats interval when 0 so legacy deployments
            # keep their existing behaviour.
            # EMERGENCY KILL SWITCH — set `OMNIGRID_DISABLE_SNMP=1`
            # in `.env` to completely short-circuit the SNMP sampler
            # tick (no probes fire, no provider state changes). Use
            # this when SNMP is causing event-loop starvation /
            # healthcheck flap and you need to bring the container
            # back up before fixing the root cause via the SPA.
            # Reads via `env_get(EnvKey.X)` (NOT through TUNABLES /
            # settings) so it works even if the DB is unreachable.
            # The typed-enum lookup guarantees a typo'd env var name
            # fails import-time (the rule "every key reference MUST
            # use Tunable / Settings / EnvKey enums") — pre-fix a
            # bare `os.environ.get("OMNIGRID_DISABLE_SMNP")` typo
            # would silently return empty and the kill-switch
            # wouldn't fire.
            from logic.env_keys import EnvKey as _EnvKey, env_get as _env_get
            if _env_get(_EnvKey.OMNIGRID_DISABLE_SNMP).strip() in ("1", "true", "yes"):
                # Skip the entire SNMP block this tick. Quiet — no
                # per-tick log spam; the operator knows they set it.
                pass
            elif "snmp" in active:
                snmp_interval = tuning.tuning_int(Tunable.SNMP_SAMPLE_INTERVAL_SECONDS)
                snmp_due = (snmp_interval <= 0) or (now_ts - last_snmp_ts >= snmp_interval)
                if snmp_due:
                    snmp_hosts = _load_curated_snmp_hosts()
                    if snmp_hosts:
                        # Defence-in-depth hard cap — even if the
                        # operator's DB has a stale tunable override
                        # of 16+, we cap at 4 because that's the
                        # event-loop-safe value. Operators on small
                        # reliable fleets can still raise via the
                        # tunable but the floor is the structural
                        # batching below, which always yields
                        # between batches regardless of cap.
                        snmp_conc = max(1, min(4, tuning.tuning_int(Tunable.SNMP_CONCURRENCY)))
                        snmp_sem = asyncio.Semaphore(snmp_conc)
                        # Thundering-herd guard — process hosts in
                        # batches of `snmp_conc` with a brief asyncio
                        # yield between batches so the event loop
                        # stays responsive for /api/* requests +
                        # /api/healthz + the SSE heartbeat even when
                        # a fleet of unreachable SNMP hosts each hold
                        # the wall-clock budget (60s default). Without
                        # this, the SPA hits 504 Gateway Time-out and
                        # the Docker swarm healthcheck flaps — the
                        # Semaphore alone doesn't help because all
                        # tasks get CREATED at once and the first N
                        # all wake simultaneously when their UDP
                        # timeouts fire together.
                        for i in range(0, len(snmp_hosts), snmp_conc):
                            batch = snmp_hosts[i:i + snmp_conc]
                            await asyncio.gather(
                                *(_probe_one_snmp(h, snmp_sem) for h in batch),
                                return_exceptions=True,
                            )
                            # Brief yield between batches — lets the
                            # event loop dispatch a backlog of pending
                            # HTTP requests + SSE events before the
                            # next SNMP wave fires.
                            if i + snmp_conc < len(snmp_hosts):
                                await asyncio.sleep(0.05)
                    last_snmp_ts = now_ts
            interval = _resolve_outer_interval()
            days = tuning.tuning_int(Tunable.STATS_HISTORY_DAYS)
            if tick % max(1, 3600 // interval) == 0:
                # the hourly prune runs five large DELETE ... WHERE
                # ts<? on the densest sample tables; on a long-lived large
                # fleet that's hundreds of thousands of rows AND — because the
                # indexes are (host_id, ts DESC) composites, so a ts-only
                # predicate can't seek the leading column — a full scan. Offload
                # it to a worker thread so a multi-second prune can't stall the
                # event loop (+ the SSE heartbeat + /api/healthz — the 502-flap
                # class). _prune_old_samples stays synchronous; to_thread gives
                # it its own per-call db_conn inside the worker thread.
                # Route through `prune_with_metrics` so the Stats →
                # Samplers panel surfaces per-prune row count +
                # duration trends. Operators spot a prune that
                # started taking 5s when it used to take 50ms before
                # it triggers healthcheck flap.
                from logic.sampler_metrics import prune_with_metrics
                n = await prune_with_metrics("host_metrics_sampler", _prune_old_samples)
                if n:
                    print(f"[host_metrics_sampler] pruned {n} rows older than "
                          f"{days}d")
        # noinspection PyBroadException
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as e: # noqa: BLE001
            _tick_ok = False
            _tick_err = type(e).__name__
            print(f"[host_metrics_sampler] tick error: {e}")
        finally:
            _record_tick(
                "host_metrics_sampler",
                (time.perf_counter() - _tick_t0) * 1000.0,
                ok=_tick_ok,
                error=_tick_err,
            )
        tick += 1
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


def _shape_row(r) -> dict:
    """Turn one DB row (sqlite3.Row) into the dict shape both readers
    return. Centralised here so adding a new column is a one-line edit."""
    return {
        "ts": int(r["ts"]),
        "cpu_percent": (float(r["cpu_percent"]) if r["cpu_percent"] is not None else None),
        "mem_used": (int(r["mem_used"]) if r["mem_used"] is not None else None),
        "mem_total": (int(r["mem_total"]) if r["mem_total"] is not None else None),
        "disk_used": (int(r["disk_used"]) if r["disk_used"] is not None else None),
        "disk_total": (int(r["disk_total"]) if r["disk_total"] is not None else None),
        "net_rx_bps": (float(r["net_rx_bps"]) if r["net_rx_bps"] is not None else None),
        "net_tx_bps": (float(r["net_tx_bps"]) if r["net_tx_bps"] is not None else None),
        "disk_read_bps": (float(r["disk_read_bps"]) if r["disk_read_bps"] is not None else None),
        "disk_write_bps": (float(r["disk_write_bps"]) if r["disk_write_bps"] is not None else None),
    }


_SAMPLES_COLS = (
    "ts, cpu_percent, mem_used, mem_total, "
    "disk_used, disk_total, net_rx_bps, net_tx_bps, "
    "disk_read_bps, disk_write_bps"
)


def recent_samples(host_id: str, since_ts: int, limit: int = 500) -> list[dict]:
    """Return rows for one host back to ``since_ts`` (epoch s), oldest-first."""
    if not host_id:
        return []
    try:
        with db_conn() as c:
            rows = c.execute(
                f"SELECT {_SAMPLES_COLS} "
                "FROM host_metrics_samples "
                "WHERE host_id=? AND ts >= ? "
                "ORDER BY ts ASC LIMIT ?",
                (host_id, int(since_ts), int(limit)),
            ).fetchall()
    # noinspection PyBroadException
    except Exception as e: # noqa: BLE001
        print(f"[host_metrics_sampler] recent_samples({host_id!r}) failed: {e}")
        return []
    return [_shape_row(r) for r in rows]


# noinspection DuplicatedCode
def last_samples(host_id: str, limit: int = 5) -> list[dict]:
    """Newest-first recent rows for the debug endpoint."""
    if not host_id:
        return []
    try:
        with db_conn() as c:
            rows = c.execute(
                f"SELECT {_SAMPLES_COLS} "
                "FROM host_metrics_samples WHERE host_id=? "
                "ORDER BY ts DESC LIMIT ?",
                (host_id, int(limit)),
            ).fetchall()
    # noinspection PyBroadException
    except Exception as e: # noqa: BLE001
        print(f"[host_metrics_sampler] last_samples({host_id!r}) failed: {e}")
        return []
    return [_shape_row(r) for r in rows]


def series_collectors_present(host_id: str, hours: int) -> dict:
    """Did the sampler EVER record a non-null disk-I/O / net rate for
    this host in the given window?

    Used by the host drawer's empty-state copy to distinguish:
      - "No activity in this window" — collector IS reporting, just idle.
      - "Exporter doesn't expose `node_disk_*`" — collector is permanently
        missing on this host's node-exporter (operator needs to enable
        ``--collector.diskstats`` / ``--collector.netstats``).

    ``history_series`` masks NULL → 0 to keep the chart math simple, so
    the SPA can't tell the two cases apart from the series alone. This
    helper walks the raw rows and reports per-metric whether any
    non-null value exists. The frontend gates collector-missing copy on
    the result. False here means "every sample had NULL for that key" —
    a strong signal the collector isn't enabled (the alternative
    explanation is "two-tick window too short to compute any rate",
    but two ticks at 60–300s cadence is enough to rule that out).

    Filesystem and memory keys (`mem_total`, `disk_total`) round out
    the diagnostic: an exporter with ONLY the meminfo + filesystem
    collectors enabled produces a row where mem/disk gauges are set
    but rate fields are NULL — that's the canonical "stripped exporter"
    signal we want to surface.
    """
    hours = max(1, min(168, int(hours or 1)))
    since = int(time.time() - hours * 3600)
    raw = recent_samples(host_id, since, limit=hours * 60)
    has = {"disk_io": False, "net": False, "fs": False, "mem": False, "cpu": False}
    for r in raw:
        if r.get("disk_read_bps") is not None or r.get("disk_write_bps") is not None:
            has["disk_io"] = True
        if r.get("net_rx_bps") is not None or r.get("net_tx_bps") is not None:
            has["net"] = True
        if r.get("disk_total"):
            has["fs"] = True
        if r.get("mem_total"):
            has["mem"] = True
        if r.get("cpu_percent") is not None:
            has["cpu"] = True
    return has


# noinspection DuplicatedCode,PyTypeChecker
def history_series(host_id: str, hours: int) -> list[dict]:
    """Read rows from the table and shape them as a Beszel-compatible series.

    Returns a list of points whose keys mirror what
    :func:`logic.beszel.fetch_system_history` emits, so the frontend's
    chart helpers (`hostChart`, `hostMetricStats`, `hostChartMax`) work
    against this path with no branching. Fields NE doesn't have are
    returned as 0; the chart-gates ``hostMetricStats(...).maxRaw > 0``
    on the SPA side hide those panels cleanly.

    For windows longer than 24h the raw 5-min samples are aggregated
    down to ~96 buckets server-side via AVG-per-bucket so the SVG
    renderer doesn't get a 2000-point noise wall on the 7d view.
    Mirrors the bucketing pattern `api_hosts_snmp_history` uses.
    """
    hours = max(1, min(168, int(hours or 1)))
    since = int(time.time() - hours * 3600)
    raw = recent_samples(host_id, since, limit=hours * 60)
    # Server-side bucket for long windows. Same target (~96 points)
    # as the SNMP history endpoint so every chart family aggregates
    # uniformly at the 7d range.
    if hours > 24 and raw:
        bucket_seconds = max(60, int(hours * 3600 / 96))
        buckets: dict[int, list[dict]] = {}
        for row in raw:
            ts = int(row.get("ts") or 0)
            if ts <= 0:
                continue
            key = ts // bucket_seconds
            buckets.setdefault(key, []).append(row)
        aggregated: list[dict] = []
        for key in sorted(buckets.keys()):
            rows = buckets[key]
            agg: dict = {}
            # Average every numeric field across the bucket; use MIN(ts)
            # as the canonical bucket timestamp. Non-numeric fields take
            # the latest row's value.
            min_ts = min(int(r.get("ts") or 0) for r in rows)
            sample_keys = set()
            for r in rows:
                sample_keys.update(r.keys())
            for k in sample_keys:
                if k == "ts":
                    agg[k] = min_ts
                    continue
                # Build the numeric-only list with explicit append loop —
                # the comprehension form `[r.get(k) for r in rows if
                # isinstance(r.get(k), (int, float))]` doesn't narrow
                # the element type for pyright, so the downstream `sum()`
                # rejects it as `list[Unknown | None]`.
                vals: list[float] = []
                for r in rows:
                    _v = r.get(k)
                    if isinstance(_v, (int, float)):
                        vals.append(float(_v))
                if vals:
                    agg[k] = sum(vals) / len(vals)
                else:
                    # Non-numeric (string / list) — keep the latest
                    # row's value (rows are ASC-sorted from recent_samples).
                    agg[k] = rows[-1].get(k)
            aggregated.append(agg)
        raw = aggregated
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
        # Disk I/O rates added in backfilled to 0 for rows
        # written before the column existed, so old history points
        # render flat until the new sampler ticks land.
        dr = r.get("disk_read_bps") or 0.0
        dw = r.get("disk_write_bps") or 0.0
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
            "dr": dr,
            "dw": dw,
            # Swap + load avg still not surfaced by the NE sampler.
            # Future work could fold them in (gauges for `node_load1` /
            # `node_memory_Swap*`). Returning zeros keeps the frontend
            # gates honest — those cards stay hidden until real data
            # lands here.
            "la1": 0.0,
            "la5": 0.0,
            "la15": 0.0,
            "s": 0.0,
            "su": 0.0,
        })
    return series


# ---------------------------------------------------------------------------
# Smoke test — run this module directly to exercise _compute_row end-to-end
# against a hand-rolled minimal node-exporter response. Not a pytest fixture
# (project has no test runner); invocation is ``python -m logic.host_metrics_sampler``
# or ``python logic/host_metrics_sampler.py``. Exits 0 on pass.
# ---------------------------------------------------------------------------
def _smoke_test() -> int:
    """Standalone parser smoke-test over a fixture payload; returns a process
    exit code (0 = pass). Run via ``python logic/host_metrics_sampler.py``."""
    fixture = """\
# minimal node-exporter response covering the fields _compute_row reads
node_memtotal_bytes 8589934592
node_memory_MemAvailable_bytes 4294967296
node_filesystem_size_bytes{mountpoint="/",fstype="ext4",device="/dev/sda1"} 107374182400
node_filesystem_avail_bytes{mountpoint="/",fstype="ext4",device="/dev/sda1"} 53687091200
node_network_receive_bytes_total{device="eth0"} 1000000
node_network_transmit_bytes_total{device="eth0"} 500000
node_disk_read_bytes_total{device="sda"} 4000000
node_disk_written_bytes_total{device="sda"} 2000000
node_disk_read_bytes_total{device="sda1"} 4000000
node_disk_written_bytes_total{device="sda1"} 2000000
node_disk_read_bytes_total{device="loop0"} 99999999
node_disk_written_bytes_total{device="loop0"} 99999999
node_cpu_seconds_total{cpu="0",mode="idle"} 1234.5
node_cpu_seconds_total{cpu="1",mode="idle"} 2345.6
node_uname_info{sysname="Linux",release="5.15.0",machine="x86_64"} 1
node_boot_time_seconds 1700000000
"""
    parsed = _ne.parse_exporter_text(fixture)
    net = _ne.parse_network_counters(fixture)
    parsed["host_net_rx_total"] = net["total_rx"]
    parsed["host_net_tx_total"] = net["total_tx"]
    disk = _ne.parse_disk_counters(fixture)
    parsed["host_disk_read_total"] = disk["total_read"]
    parsed["host_disk_write_total"] = disk["total_written"]

    # Disk parser sanity — sda1 is a partition of sda → MUST be excluded
    # from the totals (else we double-count). loop0 is excluded as a
    # synthetic device. Only sda's 4 MB / 2 MB should land in totals.
    assert disk["total_read"] == 4000000, f"disk total_read={disk['total_read']}"
    assert disk["total_written"] == 2000000, f"disk total_written={disk['total_written']}"
    dev_names = [d["name"] for d in disk["devices"]]
    assert dev_names == ["sda"], f"expected only sda, got {dev_names}"

    # dm-* / md* are NO LONGER excluded (Synology / NAS use
    # them as the user-facing volumes). Verify they're counted now.
    nas_fixture = """\
node_disk_read_bytes_total{device="dm-0"} 1000000
node_disk_written_bytes_total{device="dm-0"} 500000
node_disk_read_bytes_total{device="md0"}  2000000
node_disk_written_bytes_total{device="md0"} 1000000
node_disk_read_bytes_total{device="loop0"} 99999999
node_disk_written_bytes_total{device="loop0"} 99999999
"""
    nas_disk = _ne.parse_disk_counters(nas_fixture)
    assert nas_disk["total_read"] == 3000000, f"NAS dm+md should be counted: {nas_disk}"
    assert nas_disk["total_written"] == 1500000
    nas_names = [d["name"] for d in nas_disk["devices"]]
    assert "dm-0" in nas_names and "md0" in nas_names and "loop0" not in nas_names

    # No-devices case: exporter without the diskstats
    # collector returns no node_disk_* lines → parser returns None
    # totals → probe_node leaves the host_disk_*_total keys absent →
    # sampler stores NULL rate (not 0). Critical: distinguishing
    # "missing data" from "zero activity" so the chart correctly
    # displays "no data" instead of a flat zero line.
    no_disk_fixture = """\
node_memtotal_bytes 8589934592
node_filesystem_size_bytes{mountpoint="/",device="/dev/sda1"} 100
node_filesystem_avail_bytes{mountpoint="/",device="/dev/sda1"} 50
node_network_receive_bytes_total{device="eth0"} 1000
node_network_transmit_bytes_total{device="eth0"} 500
"""
    no_disk_parsed = _ne.parse_disk_counters(no_disk_fixture)
    assert no_disk_parsed["total_read"] is None, "no-devices must return None"
    assert no_disk_parsed["total_written"] is None

    # FreeBSD fallback : hosts running the FreeBSD node-exporter
    # port emit `node_devstat_bytes_total{device,type}` instead of
    # `node_disk_*`. Real opnsense scrape sample: ada0 = 4.12 GB
    # read / 14.82 TB write totals, plus a synthetic md98 (memdisk) and
    # pass0 (SCSI passthrough) that MUST be excluded. Verifies the
    # parser falls through to the FreeBSD branch when the Linux family
    # returns no devices, AND that the correct synthetic-device
    # exclusion list is applied (pass*/md*/cd*).
    bsd_fixture = """\
node_devstat_bytes_total{device="ada0",type="read"} 4119181824
node_devstat_bytes_total{device="ada0",type="write"} 14823682183168
node_devstat_bytes_total{device="md98",type="read"} 76800
node_devstat_bytes_total{device="md98",type="write"} 0
node_devstat_bytes_total{device="pass0",type="read"} 0
node_devstat_bytes_total{device="pass0",type="write"} 0
"""
    bsd_disk = _ne.parse_disk_counters(bsd_fixture)
    assert bsd_disk is not None, "BSD disk-counters fixture should parse to a dict"
    assert bsd_disk["total_read"] == 4119181824, f"BSD read mismatch: {bsd_disk}"
    assert bsd_disk["total_written"] == 14823682183168, f"BSD write mismatch: {bsd_disk}"
    bsd_names = [d["name"] for d in bsd_disk["devices"]]
    assert bsd_names == ["ada0"], f"only ada0 should pass BSD exclusion, got {bsd_names}"

    # FreeBSD-fallback hand-off into the sampler: rate calc must work
    # the same way regardless of which metric family produced the
    # totals — `probe_node` writes `host_disk_*_total` keys identically
    # for both, so the sampler's `_compute_row` is family-agnostic. We
    # exercise that here with synthetic before/after BSD totals.
    bsd_t0 = 1700000000.0
    bsd_stats_before = _ne.parse_exporter_text(bsd_fixture)
    bsd_stats_before["host_net_rx_total"] = 0
    bsd_stats_before["host_net_tx_total"] = 0
    bsd_stats_before["host_disk_read_total"] = bsd_disk["total_read"]
    bsd_stats_before["host_disk_write_total"] = bsd_disk["total_written"]
    bsd_baseline_row, bsd_prev = _compute_row(
        "bsd_host", bsd_t0, bsd_stats_before, None,
    )
    assert bsd_prev is not None
    assert bsd_prev[3] == 4119181824 and bsd_prev[4] == 14823682183168, bsd_prev
    bsd_stats_after = dict(bsd_stats_before)
    bsd_stats_after["host_disk_read_total"] += 6 * 1024 * 1024
    bsd_stats_after["host_disk_write_total"] += 3 * 1024 * 1024
    bsd_row, _ = _compute_row("bsd_host", bsd_t0 + 300, bsd_stats_after, bsd_prev)
    assert bsd_row is not None
    assert abs(bsd_row["disk_read_bps"] - (6 * 1024 * 1024) / 300) < 0.001, bsd_row
    assert abs(bsd_row["disk_write_bps"] - (3 * 1024 * 1024) / 300) < 0.001, bsd_row

    # Linux pass takes precedence: a host that emits BOTH families
    # (rare — would have to be a hand-written exporter) must NOT
    # double-count. The FreeBSD branch only runs when the Linux pass
    # produces zero devices.
    mixed_fixture = """\
node_disk_read_bytes_total{device="sda"} 1000
node_disk_written_bytes_total{device="sda"} 500
node_devstat_bytes_total{device="ada0",type="read"}  9999999
node_devstat_bytes_total{device="ada0",type="write"} 9999999
"""
    mixed_disk = _ne.parse_disk_counters(mixed_fixture)
    assert mixed_disk is not None, "mixed disk-counters fixture should parse to a dict"
    assert mixed_disk["total_read"] == 1000, f"Linux pass must win: {mixed_disk}"
    assert mixed_disk["total_written"] == 500
    mixed_names = [d["name"] for d in mixed_disk["devices"]]
    assert mixed_names == ["sda"], f"BSD branch must not run: {mixed_names}"

    # Tick 1 — no previous counter, should establish baseline. Gauges
    # are meaningful so a row IS produced; rates simply absent.
    t0 = 1700000000.0
    row1, next1 = _compute_row("h1", t0, parsed, None)
    assert next1 is not None
    assert next1 == (t0, 1000000, 500000, 4000000, 2000000), f"baseline mismatch: {next1}"
    assert row1 is not None and row1["net_rx_bps"] is None and row1["net_tx_bps"] is None
    assert row1["disk_read_bps"] is None and row1["disk_write_bps"] is None
    assert row1["mem_total"] == 8589934592
    assert row1["mem_used"] == 8589934592 - 4294967296
    assert row1["disk_total"] == 107374182400
    assert row1["disk_used"] == 107374182400 - 53687091200

    # Tick 2 — net counters bumped by 5 MB rx / 1 MB tx, disk bumped by
    # 6 MB read / 3 MB write, all over 5 minutes.
    bumped = dict(parsed)
    bumped["host_net_rx_total"] = 1000000 + 5 * 1024 * 1024
    bumped["host_net_tx_total"] = 500000 + 1 * 1024 * 1024
    bumped["host_disk_read_total"] = 4000000 + 6 * 1024 * 1024
    bumped["host_disk_write_total"] = 2000000 + 3 * 1024 * 1024
    t1 = t0 + 300
    row2, next2 = _compute_row("h1", t1, bumped, next1)
    assert row2 is not None
    assert next2 is not None
    assert abs(row2["net_rx_bps"] - (5 * 1024 * 1024) / 300) < 0.001, row2["net_rx_bps"]
    assert abs(row2["net_tx_bps"] - (1 * 1024 * 1024) / 300) < 0.001, row2["net_tx_bps"]
    assert abs(row2["disk_read_bps"] - (6 * 1024 * 1024) / 300) < 0.001, row2["disk_read_bps"]
    assert abs(row2["disk_write_bps"] - (3 * 1024 * 1024) / 300) < 0.001, row2["disk_write_bps"]

    # Tick 3 — net counter rollback (reboot) but disk counters keep
    # advancing normally. Disk rates should compute; net rates skip.
    # Validates the INDEPENDENCE of the two rate pairs.
    mixed = dict(parsed)
    mixed["host_net_rx_total"] = 100  # post-reboot
    mixed["host_net_tx_total"] = 50
    mixed["host_disk_read_total"] = next2[3] + 1024 * 1024  # +1 MB read
    mixed["host_disk_write_total"] = next2[4] + 512 * 1024  # +512 KB write
    t2 = t1 + 300
    row3, next3 = _compute_row("h1", t2, mixed, next2)
    assert row3 is not None
    assert next3 is not None
    assert row3["net_rx_bps"] is None, "net rollback must skip"
    assert row3["net_tx_bps"] is None
    assert row3["disk_read_bps"] is not None, "disk pair must compute when its delta is in bounds"
    assert row3["disk_write_bps"] is not None
    assert next3 == (t2, 100, 50, next2[3] + 1024 * 1024, next2[4] + 512 * 1024)

    # Tick 4 — disk counter wrap (50 GB jump). Disk rates must skip;
    # net rates ALSO skip because we just rebaselined them in tick 3
    # (so prev_ts is t2 → delta_s ok, but prev_rx=100 → +very small ok).
    # Actually net deltas WILL compute small positive values here; we
    # only assert disk pair behaviour.
    wrap = dict(parsed)
    wrap["host_net_rx_total"] = 100 + 2048
    wrap["host_net_tx_total"] = 50 + 1024
    # Re-assert narrowing so pyright sees the indexed access as safe —
    # the comparison on the previous line doesn't carry the narrowing
    # forward through the dict update.
    assert next3 is not None
    wrap["host_disk_read_total"] = next3[3] + (50 * 1024 * 1024 * 1024)  # 50 GB
    wrap["host_disk_write_total"] = next3[4] + 1024
    t3 = t2 + 300
    row4, next4 = _compute_row("h1", t3, wrap, next3)
    assert row4 is not None
    assert next4 is not None
    assert row4["disk_read_bps"] is None, "out-of-bounds disk delta must skip"
    assert row4["disk_write_bps"] is None, (
        "single out-of-bounds field must skip BOTH disk rates"
    )
    # net should still have computed
    assert row4["net_rx_bps"] is not None and row4["net_tx_bps"] is not None

    # Tick 5 — short delta (60s window underflow). All four rates skip.
    short = dict(parsed)
    short["host_net_rx_total"] = next4[1] + 1024
    short["host_net_tx_total"] = next4[2] + 1024
    short["host_disk_read_total"] = next4[3] + 1024
    short["host_disk_write_total"] = next4[4] + 1024
    t4 = t3 + 30  # below _MIN_DELTA_SECONDS=60
    row5, _ = _compute_row("h1", t4, short, next4)
    assert row5 is not None
    assert row5["net_rx_bps"] is None and row5["net_tx_bps"] is None
    assert row5["disk_read_bps"] is None and row5["disk_write_bps"] is None

    # Pre-fix cache shape (3-tuple) — backwards compat. Disk rates
    # should skip (no prev disk anchor), net rates compute normally.
    legacy_prev = (t0, 1000000, 500000)  # missing disk fields
    legacy_bumped = dict(parsed)
    legacy_bumped["host_net_rx_total"] = 1000000 + 1 * 1024 * 1024
    legacy_bumped["host_net_tx_total"] = 500000 + 512 * 1024
    legacy_bumped["host_disk_read_total"] = 5 * 1024 * 1024
    legacy_bumped["host_disk_write_total"] = 3 * 1024 * 1024
    row6, next6 = _compute_row("h2", t0 + 300, legacy_bumped, legacy_prev)
    assert row6 is not None
    assert next6 is not None
    assert row6["net_rx_bps"] is not None, "legacy 3-tuple prev still drives net rate"
    assert row6["disk_read_bps"] is None, "no disk anchor → skip first disk rate"
    assert row6["disk_write_bps"] is None
    assert len(next6) == 5, "next_counter must be the new 5-tuple shape"

    # Empty probe — no fields at all.
    row7, next7 = _compute_row("h3", time.time(), {}, None)
    assert row7 is None and next7 is None

    # No-disk probe with a previous-tick anchor: the
    # current probe lacks host_disk_*_total entirely. Disk rates MUST
    # stay null even though prev had disk anchors. Other fields keep
    # working (net rate computes normally).
    no_disk_stats = _ne.parse_exporter_text(no_disk_fixture)
    no_disk_net_x = _ne.parse_network_counters(no_disk_fixture)
    no_disk_stats["host_net_rx_total"] = no_disk_net_x["total_rx"]
    no_disk_stats["host_net_tx_total"] = no_disk_net_x["total_tx"]
    # Deliberately NOT setting host_disk_*_total — mirrors what
    # probe_node does when parse_disk_counters returns None totals.
    fake_prev = (t0, 1, 1, 100, 50)  # has disk anchor from previous tick
    nd_row, _ = _compute_row("hno", t0 + 300, no_disk_stats, fake_prev)
    assert nd_row is not None
    assert nd_row["disk_read_bps"] is None, "missing disk metrics → null rate"
    assert nd_row["disk_write_bps"] is None

    print("[host_metrics_sampler] smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_smoke_test())
