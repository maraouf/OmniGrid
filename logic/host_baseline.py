"""Per-host rolling-baseline computer for drift detection.

Drift-from-baseline indicator. For each curated
host, computes the 30-day rolling median + IQR (interquartile range)
of four metrics — CPU% (`cpu_pct`), memory% (`mem_pct`), disk-fill%
(`disk_pct`), ping RTT in milliseconds (`ping_rtt_ms`). The Hosts
view renders a ▲ (above baseline) / ▼ (below) / ━ (within baseline)
chip next to each metric so operators can spot "is web01's CPU at
60% normal or abnormal?" at a glance — anomaly detection without ML
complexity, just statistics.

**Skip-don't-synthesize discipline.** When a host has < 50 samples in
the 30-day window, the baseline isn't computed — we don't know what
"normal" is yet. The frontend renders no chip in that case (instead
of a misleading ━ that could be confused with "within baseline").

Hourly recompute via the lifespan-managed
`host_baseline_sampler`; per-host enrichment in `_merge_one_host`
reads the cached baseline + the LIVE metric value and returns
``drift: {<metric>: '▲'|'▼'|'━'|null, ...}`` on the API row.
"""
from __future__ import annotations

import time
from typing import Optional

from logic.db import db_conn


# Curated metric roster — the set we compute baselines for. Keeping it
# small + explicit prevents drift charts for every random `host_*`
# field (operator confusion if `host_battery_temp_c` got a chip).
METRICS = ("cpu_pct", "mem_pct", "disk_pct", "ping_rtt_ms")

# Minimum sample count for IQR to be statistically meaningful. Below
# this, the metric stays unbaselined (frontend hides the chip).
_MIN_SAMPLES = 50

# Rolling window — 30 days of recent samples. Operators rarely care
# about drift beyond a month; older samples drift the baseline toward
# stale values that don't match the current workload.
_WINDOW_DAYS = 30


def _percentile(values: list[float], p: float) -> Optional[float]:
    """Linear-interpolation percentile. `values` must be sorted.

    Gate dependency note: in this module the helper is only ever called
    from `_baseline_for`, which short-circuits at `n < _MIN_SAMPLES`
    (50). The empty-list + single-element branches below are
    defence-in-depth for callers added LATER; do NOT remove them. If
    you reuse this helper from a new call site, audit the input
    invariants — the implementation assumes a sorted, non-empty list
    of numeric values and skips bounds checks beyond the single-
    element early-return.
    """
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    k = (len(values) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    if lo == hi:
        return values[lo]
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def _baseline_for(values: list[float]) -> Optional[tuple[float, float, int]]:
    """Returns (median, iqr, sample_count) or None when insufficient.
    `values` is the raw samples (unsorted); we sort here so callers
    don't have to.
    """
    n = len(values)
    if n < _MIN_SAMPLES:
        return None
    s = sorted(values)
    median = _percentile(s, 0.5) or 0.0
    q1 = _percentile(s, 0.25) or 0.0
    q3 = _percentile(s, 0.75) or 0.0
    iqr = max(0.0, q3 - q1)
    return (median, iqr, n)


def _fetch_host_metric_samples(c, host_id: str, since_ts: int) -> dict[str, list[float]]:
    """Pull every baseline-eligible sample for one host within the
    rolling window. Returns the per-metric values dict ready for
    `_baseline_for` to consume.

    One UNION ALL across the 5 CPU/mem/disk source tables plus one
    SELECT against `ping_samples`. Column-name alignment normalises
    SNMP's `cpu_used_pct` to a uniform `cpu` alias so a single SELECT
    against the union materialises the union in one pass. Per-table
    `LIMIT 20000` caps row count so a worst-case host (all 5 tables
    active, 30-day window, sub-minute sampling) can't OOM Python's
    list-append-then-sort path. ORDER BY ts DESC inside each
    sub-select keeps the NEWEST samples when the cap fires (most
    representative of current workload).

    Resilience: each sub-select is wrapped in its own try/except. A
    missing table (fresh deploy / provider never enabled) raises
    `sqlite3.OperationalError` during query compile; we exclude that
    table from the UNION and retry with the remaining tables. A
    poisoned-by-one-table UNION would otherwise mean Beszel-only or
    SNMP-only hosts get zero baseline because a sibling provider
    table doesn't exist.
    """
    out: dict[str, list[float]] = {m: [] for m in METRICS}
    # Per-table sample-row cap — 20000 ≈ 70 days at 5-min cadence per
    # table; the 30-day window can't hit it under normal sampling.
    _PER_TABLE_LIMIT = 20000

    # Table set for the CPU/mem/disk UNION. `cpu_col_expr` aligns
    # SNMP's column name to the same `cpu` alias so the union is
    # column-shape-uniform. `table_label` is the partition tag emitted
    # in the SELECT so per-table sample-count diagnostics could be
    # reconstructed downstream if needed (currently unused but
    # cheap — one extra column at SELECT time).
    _union_sources = [
        ("host_metrics_samples", "cpu_percent"),
        ("host_beszel_samples",  "cpu_percent"),
        ("host_pulse_samples",   "cpu_percent"),
        ("host_webmin_samples",  "cpu_percent"),
        ("host_snmp_samples",    "cpu_used_pct"),
    ]

    def _table_exists(table_name: str) -> bool:
        try:
            row = c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
            return row is not None
        except Exception:
            return False

    # Drop tables that don't exist BEFORE building the UNION so a
    # missing sibling can't poison the whole query. Common on fresh
    # deploys (Beszel-only operator has no host_pulse_samples yet) or
    # after disabling a provider.
    live_sources = [(t, c_col) for (t, c_col) in _union_sources if _table_exists(t)]

    if live_sources:
        # Build one UNION ALL — each sub-select normalises its CPU
        # column to the `cpu` alias and tags the row with its table
        # name so a future per-table sample-count diagnostic is
        # one-line away. `ORDER BY ts DESC LIMIT N` inside each
        # sub-select bounds memory per source.
        subqueries = []
        params: list = []
        for tbl, cpu_col in live_sources:
            subqueries.append(
                f"SELECT {cpu_col} AS cpu, mem_used, mem_total, "
                f"       disk_used, disk_total, '{tbl}' AS src "
                f"  FROM {tbl} "
                f" WHERE host_id = ? AND ts >= ? "
                f" ORDER BY ts DESC LIMIT ?"
            )
            params.extend([host_id, since_ts, _PER_TABLE_LIMIT])
        sql = " UNION ALL ".join(subqueries)
        try:
            rows = c.execute(sql, params).fetchall()
            for r in rows:
                cpu = r[0]
                if cpu is not None:
                    out["cpu_pct"].append(float(cpu))
                mu, mt = r[1], r[2]
                if mu is not None and mt and mt > 0:
                    out["mem_pct"].append(float(mu) / float(mt) * 100.0)
                du, dt = r[3], r[4]
                if du is not None and dt and dt > 0:
                    out["disk_pct"].append(float(du) / float(dt) * 100.0)
        except Exception:
            # Defensive — `_table_exists` should have filtered every
            # missing table, but a column-schema mismatch (e.g. legacy
            # deploy that never ran the additive ALTER TABLE for
            # disk_used/disk_total) could still raise here. Skip
            # silently rather than dropping the ping branch below.
            pass

    # Ping RTT — separate table with its own column shape; a UNION
    # with the CPU/mem/disk set would require padding columns and
    # complicates the result-tuple unpack. Kept as its own SELECT.
    if _table_exists("ping_samples"):
        try:
            rows = c.execute(
                "SELECT rtt_ms FROM ping_samples "
                " WHERE host_id = ? AND ts >= ? AND rtt_ms IS NOT NULL "
                " ORDER BY ts DESC LIMIT ?",
                (host_id, since_ts, _PER_TABLE_LIMIT),
            ).fetchall()
            for r in rows:
                v = r[0]
                if v is not None:
                    out["ping_rtt_ms"].append(float(v))
        except Exception:
            pass
    return out


def compute_baselines(host_id: str) -> dict[str, dict]:
    """Recompute baselines for ONE host. UPSERTs into `host_baselines`
    AND returns the in-memory map for the caller (sampler logs it).
    """
    if not host_id:
        return {}
    since_ts = int(time.time() - _WINDOW_DAYS * 86400)
    out: dict[str, dict] = {}
    try:
        with db_conn() as c:
            samples = _fetch_host_metric_samples(c, host_id, since_ts)
            for metric, vals in samples.items():
                bl = _baseline_for(vals)
                if bl is None:
                    # Drop the row so a metric that USED to be
                    # baselined but no longer has enough samples
                    # doesn't keep returning a stale baseline.
                    c.execute(
                        "DELETE FROM host_baselines WHERE host_id = ? AND metric = ?",
                        (host_id, metric),
                    )
                    continue
                median, iqr, n = bl
                c.execute(
                    "INSERT OR REPLACE INTO host_baselines "
                    "(host_id, metric, median, iqr, sample_count, computed_ts) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (host_id, metric, median, iqr, n, int(time.time())),
                )
                out[metric] = {
                    "median": median, "iqr": iqr, "sample_count": n,
                }
            c.commit()
    except (KeyboardInterrupt, SystemExit):
        # NEVER swallow lifecycle exceptions — the asyncio sampler
        # task relies on CancelledError propagating to honour
        # shutdown signals (it's a sync function called from the
        # async sampler loop, but propagating the same family of
        # interpreter-control exceptions is the documented contract).
        raise
    except Exception as e:
        print(f"[host_baseline] {host_id} compute failed: {e}")
    return out


def load_baselines(host_id: str) -> dict[str, dict]:
    """Read the cached baseline for one host. Returns
    `{metric: {median, iqr, sample_count, computed_ts}}` — empty when
    no baselines exist yet (fresh deploy / sampler hasn't run).
    """
    if not host_id:
        return {}
    out: dict[str, dict] = {}
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT metric, median, iqr, sample_count, computed_ts "
                "  FROM host_baselines WHERE host_id = ?",
                (host_id,),
            ).fetchall()
            for r in rows:
                out[r[0]] = {
                    "median":       float(r[1]) if r[1] is not None else None,
                    "iqr":          float(r[2]) if r[2] is not None else None,
                    "sample_count": int(r[3]) if r[3] is not None else 0,
                    "computed_ts":  int(r[4]) if r[4] is not None else 0,
                }
    except (KeyboardInterrupt, SystemExit):
        # See note in `compute_baselines` — propagate interpreter-
        # control exceptions so the asyncio caller's cancellation
        # contract still holds.
        raise
    except Exception as e:
        print(f"[host_baseline] {host_id} load failed: {e}")
    return out


def drift_indicator(value: Optional[float], baseline: dict) -> Optional[str]:
    """Classify ONE metric's drift state vs. the baseline. Returns:
      - '▲' when value > median + 1 × IQR
      - '▼' when value < median - 1 × IQR
      - '━' when within 1 IQR of median
      - None when value or baseline is missing / IQR is degenerate
    """
    if value is None or not isinstance(baseline, dict):
        return None
    median = baseline.get("median")
    iqr = baseline.get("iqr")
    if median is None or iqr is None:
        return None
    # Degenerate IQR (all samples identical) → can't classify drift
    # meaningfully. Returning None hides the chip.
    if iqr <= 0:
        return None
    delta = float(value) - float(median)
    if delta > float(iqr):
        return "▲"
    if delta < -float(iqr):
        return "▼"
    return "━"


def host_drift_for_api(host_id: str, live_metrics: dict) -> dict:
    """Compose the `drift` dict that `_shape_host_api_row` forwards
    to the SPA. `live_metrics` is the merged host dict (host_*
    fields). Output keys mirror `METRICS` so the SPA helper indexes
    cleanly.
    """
    if not host_id:
        return {}
    bl = load_baselines(host_id)
    if not bl:
        return {}
    # Compute LIVE metric values from the merged dict in the same
    # shape the baseline computer used.
    cpu = live_metrics.get("host_cpu_percent")
    mem_used = live_metrics.get("host_mem_used")
    mem_total = live_metrics.get("host_mem_total")
    disk_used = live_metrics.get("host_disk_used")
    disk_total = live_metrics.get("host_disk_total")
    ping_rtt = live_metrics.get("host_ping_rtt_ms")
    live = {
        "cpu_pct":      float(cpu) if cpu is not None else None,
        "mem_pct":      (float(mem_used) / float(mem_total) * 100.0)
                          if mem_used is not None and mem_total else None,
        "disk_pct":     (float(disk_used) / float(disk_total) * 100.0)
                          if disk_used is not None and disk_total else None,
        "ping_rtt_ms":  float(ping_rtt) if ping_rtt is not None else None,
    }
    out: dict[str, dict] = {}
    for metric in METRICS:
        ind = drift_indicator(live.get(metric), bl.get(metric, {}))
        if ind is None:
            continue
        b = bl.get(metric, {})
        out[metric] = {
            "indicator":    ind,
            "value":        live.get(metric),
            "median":       b.get("median"),
            "iqr":          b.get("iqr"),
            "sample_count": b.get("sample_count"),
            "computed_ts":  b.get("computed_ts"),
        }
    return out
