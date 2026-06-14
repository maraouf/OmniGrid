"""Nginx Proxy Manager config-drift retention sampler.

NPM exposes no request-volume / traffic metric via its admin API (that lives in
the nginx access logs, out of reach), so the only chartable signal is CONFIG
DRIFT: the proxy-host count growing over time + the security-posture gauges
(plain-HTTP hosts, certs-expiring) drifting. This lifespan sampler snapshots each
configured NPM chip per tick into ``npm_samples`` so the card can answer "you
added N proxy hosts this month" + "plain-HTTP sites have crept up".

``proxy_hosts`` is read as each day's LAST (a config-growth line); ``plain_http``
/ ``certs_expiring`` / ``dead_hosts`` as each day's MAX (the drift signals).
Cadence ``tuning_npm_sample_interval_seconds`` (0 = inherit the global stats
interval); retention ``tuning_npm_history_days`` (default 180 — config drift is a
long-horizon story). Dormant-cheap when no NPM chip is configured. Generic tick /
instance-enum / cadence resolve delegate to the shared ``logic/apps/_common.py``
sampler scaffolding; the per-app probe-write + trend math are NPM-specific.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Optional

from logic import tuning as _tuning
from logic.apps._common import (
    resolve_sample_interval, run_sampler_tick, sampler_instances)
from logic.coerce import safe_int
from logic.db import db_conn
from logic.tuning import Tunable as _Tunable

_SLUG = "nginx-proxy-manager"


def _instances() -> list:
    """Configured NPM chips (delegates to the shared sampler helper)."""
    return sampler_instances(_SLUG, "npm_sampler")


def _resolve_interval() -> int:
    """Sample cadence (s): the dedicated tunable, or — when 0 — the global stats
    interval (floored at 60s)."""
    return resolve_sample_interval(_Tunable.NPM_SAMPLE_INTERVAL_SECONDS)


# noinspection DuplicatedCode
async def _probe_one(host_id: str, service_idx: int,
                     host_row: dict, chip: dict) -> None:
    """Snapshot one NPM host's proxy-host / certs-expiring / plain-HTTP /
    dead-host counts. A host that's down / unreachable skips the write (no
    phantom 0 row); a code bug also skips."""
    try:
        from logic.apps import nginx_proxy_manager as _npm  # noqa: PLC0415
        data = await _npm.fetch_data(host_row, chip, host_id=host_id,
                                     service_idx=int(service_idx), force=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[npm_sampler] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[npm_sampler] probe {host_id}#{service_idx} error: "
              f"{type(e).__name__}: {e}")
        return
    if not isinstance(data, dict) or not data.get("available"):
        return
    row = (int(time.time()), host_id, int(service_idx),
           safe_int(data.get("proxy_hosts")), safe_int(data.get("certs_expiring")),
           safe_int(data.get("proxy_plain_http")), safe_int(data.get("dead_hosts")))
    try:
        with db_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO npm_samples "
                "(ts, host_id, service_idx, proxy_hosts, certs_expiring, "
                "plain_http, dead_hosts) VALUES (?,?,?,?,?,?,?)", row)
    except Exception as e:  # noqa: BLE001
        print(f"[npm_sampler] write {host_id}#{service_idx} failed: {e}")


# noinspection DuplicatedCode
async def _tick(tick: int) -> None:
    """Per-tick body (delegates to the shared generic sampler tick: probe every
    NPM host + the hourly retention prune)."""
    await run_sampler_tick(
        tick, instances_fn=_instances, probe_fn=_probe_one,
        interval_fn=_resolve_interval, log_tag="npm_sampler",
        prune_table="npm_samples",
        history_days_tunable=_Tunable.NPM_HISTORY_DAYS)


async def npm_sampler_loop() -> None:
    """Lifespan-managed sampler. Ticks every ``_resolve_interval()`` seconds;
    dormant-cheap when no NPM chip is configured (keeps ticking so a runtime pin
    takes effect without a restart)."""
    from logic.sampler_loop import lifespan_sampler_loop  # noqa: PLC0415
    await lifespan_sampler_loop(
        "npm_sampler",
        _tick,
        _resolve_interval,
        first_tick_delay=min(30, _resolve_interval()),
    )


# noinspection DuplicatedCode
def trend_summary(host_id: str, service_idx: int,
                  days: Optional[int] = None, *, max_points: int = 90) -> dict:
    """Config-drift trend for one NPM chip. Returns ``{days, samples,
    latest_hosts, hosts_week_change, peak_plain_http, series_hosts,
    series_plain_http}``.

    ``series_hosts`` is each day's LAST proxy-host count (the config-growth
    line); ``series_plain_http`` is each day's MAX plain-HTTP-host count (the
    security-drift line). ``hosts_week_change`` is the latest proxy-host count
    minus the count ~7 days ago (positive = growing). Each series is up to
    ``max_points`` points (oldest-first, days WITH data only). Zeroed shape when
    no samples yet — never raises."""
    win = int(days) if days else _tuning.tuning_int(_Tunable.NPM_HISTORY_DAYS)
    out: dict = {"days": int(win), "samples": 0, "latest_hosts": 0,
                 "hosts_week_change": 0, "peak_plain_http": 0,
                 "series_hosts": [], "series_plain_http": []}
    if not host_id:
        return out
    cutoff = int(time.time()) - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, proxy_hosts, plain_http "
                "FROM npm_samples WHERE host_id=? AND service_idx=? AND ts >= ? "
                "ORDER BY ts ASC", (host_id, int(service_idx), cutoff),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[npm_sampler] trend_summary({host_id}#{service_idx}) failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    out["latest_hosts"] = safe_int(rows[-1]["proxy_hosts"])
    out["peak_plain_http"] = max(safe_int(r["plain_http"]) for r in rows)
    # Per-day roll-up: LAST proxy-host count + MAX plain-HTTP count.
    host_last: dict = {}
    plain_max: dict = defaultdict(int)
    for r in rows:
        day = int(r["ts"]) // 86400
        host_last[day] = safe_int(r["proxy_hosts"])  # rows are ts ASC
        plain_max[day] = max(plain_max[day], safe_int(r["plain_http"]))
    ordered = sorted(host_last)
    series_hosts = [host_last[d] for d in ordered]
    series_plain = [plain_max[d] for d in ordered]
    # Week-change: latest day's host count vs the day closest to ~7 days earlier.
    last_day = ordered[-1]
    target = last_day - 7
    prior_day = min(ordered, key=lambda d: abs(d - target))
    out["hosts_week_change"] = host_last[last_day] - host_last[prior_day]
    if len(ordered) > max_points:
        stride = len(ordered) / float(max_points)
        idx = [int(i * stride) for i in range(max_points)]
        series_hosts = [series_hosts[i] for i in idx]
        series_plain = [series_plain[i] for i in idx]
    out["series_hosts"] = series_hosts
    out["series_plain_http"] = series_plain
    return out
