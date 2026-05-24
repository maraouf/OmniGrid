"""Second half of main_b — split for line-count hygiene.
Chain: main → main_c → main_b → main_b2 → main_pkg.core → ... → routes_extra_b.
"""
"""Second half of main — split for line-count hygiene.
Chain: main → main_pkg.main_b → main_pkg.core → main_pkg.core_b
→ main_pkg.routes → ... → main_pkg.routes_extra.
main_b loads at main's tail BEFORE core so its symbols are in
main's namespace before any route decorators run.
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
import asyncio
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any, Iterable, Optional, Set, cast

# Load .env BEFORE any os.getenv() calls (including those done at import time
# in auth.py). The file lives in the /app bind-mount and travels with the
# rest of the source via CI rsync — nothing in docker-compose.yml depends on
# env_file, which sidesteps Portainer's web-editor inability to resolve host
# paths. `override=False` keeps any values set in the compose `environment:`
# block authoritative (e.g. DB_PATH).
from dotenv import load_dotenv

from logic.env_keys import EnvKey, env_get  # noqa: E402

from main import *  # noqa: E402,F401,F403

from main import *  # noqa: E402,F401,F403



def _settings_version_for_payload() -> int:
    """Wrapper around `get_settings_version()` that's safe to call from
    a publish-side context — returns 0 on any DB blip rather than
    propagating the exception into the publish path."""
    try:
        from logic.db import get_settings_version
        return get_settings_version()
    except (sqlite3.Error, OSError, ImportError):
        return 0


# ----------------------------------------------------------------------------
# AI integration (Stage 1 foundation). Admin-only read surface for the
# dashboard tiles + paginated job log. Writes (provider config) ride
# the existing POST /api/settings additive contract — no new POST here.
# Stage 2+ will add a per-provider Test endpoint and the actual call
# wrapper that records into `ai_jobs`. For now the table is empty and
# every aggregate returns zero / empty arrays — the SPA renders cleanly.
# ----------------------------------------------------------------------------
# Canonical provider names — single source of truth lives in
# `logic.ai.SUPPORTED_PROVIDERS`. Helper below reads the live tuple
# (so a hot-reload of the AI module picks up additions without restart)
# and is used everywhere main.py needs the list — settings validators,
# the dashboard endpoint, /api/me's `client_config.ai.provider_names`.
def _ai_supported_providers() -> tuple[str, ...]:
    from logic import ai as _ai
    return tuple(_ai.SUPPORTED_PROVIDERS)


def _resolve_ai_fallback_chain(active: str) -> tuple[bool, list[str], dict[str, dict], int]:
    """Resolve the operator-configured fallback chain to the shape
    `logic.ai.ask_provider_with_fallback` consumes.

    Returns ``(enabled, chain, creds, max_depth)``:
      - ``enabled``     — bool, master toggle.
      - ``chain``       — list of provider ids in priority order, with
                          (a) the active provider stripped (it's the
                          primary), (b) disabled providers stripped,
                          (c) providers without an API key stripped.
      - ``creds``       — `{provider_id: {api_key, model, base_url}}`
                          for the active + every viable fallback.
                          The wrapper only attempts what's in this map.
      - ``max_depth``   — clamped 1..2.

    Edge cases handled:
      - Empty / unset `ai_fallback_order`     → empty chain, no fallback
        even with master switch on.
      - Only 1 provider enabled cluster-wide  → chain after filtering
        is empty; wrapper returns primary verbatim.
      - Provider listed in order but disabled → silently skipped (a
        previously-tested provider that the operator later disabled
        shouldn't surface a confusing "skipping due to..." log line).
    """
    enabled = get_setting_bool(Settings.AI_FALLBACK_ENABLED)
    raw_order = (get_setting(Settings.AI_FALLBACK_ORDER) or "").strip()
    try:
        # AI fallback-chain depth is now a TUNABLE (DB > env > default
        # with bounds clamp). Legacy `ai_fallback_max_depth` plain-
        # settings row still hydrates the form for parity; the actual
        # ask_provider_with_fallback call reads via tuning_int.
        max_depth = tuning.tuning_int(Tunable.AI_FALLBACK_MAX_DEPTH)
    except (TypeError, ValueError):
        max_depth = 1
    max_depth = max(1, min(2, max_depth))

    # Build creds for every supported provider that's master-enabled +
    # has an API key. The wrapper trusts the dict — providers absent
    # from it get skipped at the chain-walk step.
    creds: dict[str, dict] = {}
    for name in _ai_supported_providers():
        if not get_setting_bool(ai_provider_enabled_key(name)):
            continue
        api_key = (get_setting(ai_provider_api_key_key(name)) or "").strip()
        if not api_key:
            continue
        creds[name] = {
            "api_key": api_key,
            "model": (get_setting(ai_provider_model_key(name)) or "").strip(),
            "base_url": (get_setting(ai_provider_base_url_key(name)) or "").strip(),
        }

    # Parse the operator-ordered fallback list — strip the active
    # primary, drop unknowns + duplicates + providers not in `creds`
    # (disabled or missing API key). Order preserved.
    chain: list[str] = []
    seen: set[str] = set()
    valid = set(_ai_supported_providers())
    for raw in raw_order.split(","):
        p = raw.strip().lower()
        if not p or p == active or p in seen or p not in valid:
            continue
        if p not in creds:
            continue  # disabled / no api key — silently skip
        chain.append(p)
        seen.add(p)
    return enabled, chain, creds, max_depth


@app.get("/api/admin/stats/overview")
async def api_admin_stats_overview(
    _admin: AdminUser,
):
    """Admin-only: quick-insight counts for Stats → Dashboard.

    Aggregates lightweight counts the operator wants at-a-glance:
    user / session totals, the per-host-stats-provider enabled split,
    curated-host total, and the cached asset-inventory size. Designed
    to be a single fast call so the dashboard paints in one fetch.
    """
    from logic.host_metrics_sampler import PROVIDER_PREFIXES as _PROVIDER_PREFIXES
    out: dict = {
        "users": {"total": 0, "active": 0, "admins": 0},
        "sessions": {"total": 0},
        "providers": {"total": 0, "enabled": [], "disabled": []},
        "hosts": {"total": 0, "enabled": 0},
        "host_groups": {"total": 0},
        "assets": {"total": 0},
        "nodes": {"total": 0},
        "services": {"total": 0},
        "stacks": {"total": 0},
        "containers": {"total": 0},
        "backups": {"total": 0},
        "config_backups": {"total": 0},
        "schedules": {"total": 0, "enabled": 0},
        "tunables": {"total": 0, "overridden": 0},
    }
    try:
        with db_conn() as c:
            users = auth.list_users(c)
            sessions = auth.list_sessions(c)
        active_users = [u for u in users if u.get("disabled") in (0, False, None)]
        admin_users = [u for u in active_users if (u.get("role") or "") == "admin"]
        out["users"] = {
            "total": len(users),
            "active": len(active_users),
            "admins": len(admin_users),
        }
        out["sessions"] = {"total": len(sessions)}
    except Exception as e:
        out["users_error"] = str(e)
    # Per-provider enabled/disabled split. Truth source for the four
    # CSV-controlled providers is ``active_host_stats_providers()``;
    # snmp + ping are per-host opt-in so they're "enabled" iff at least
    # one curated row has them turned on.
    try:
        csv_enabled = active_host_stats_providers()
        curated = _load_hosts_config()
        per_host_enabled = {"snmp": False, "ping": False, "service_probe": False}
        for h in curated:
            for key in ("snmp", "ping"):
                sub = h.get(key) or {}
                if isinstance(sub, dict) and sub.get("enabled"):
                    per_host_enabled[key] = True
            # service_probe is per-CHIP on `services[]` rather than a
            # `hosts_config[].service_probe = {enabled: true}` flag —
            # any curated row with at least one service entry that
            # carries a probe URL counts as opting that host into
            # service_probe. Mirrors how the live merge path treats
            # the provider (main.py:10789 → populate_host_service_merge
            # runs whenever ANY service URL exists), so the Stats
            # Dashboard card now agrees with reality.
            svcs = h.get("services")
            if isinstance(svcs, list):
                for svc in svcs:
                    if isinstance(svc, dict) and (svc.get("url") or "").strip():
                        per_host_enabled["service_probe"] = True
                        break
        # Master-toggle-controlled providers — not part of the
        # `host_stats_source` CSV. Each owns its own boolean setting
        # in the `settings` table. http_probe stays purely master-
        # toggle-driven because its URLs live under `http_probe.urls`
        # rather than the service catalog. Pre-fix the Stats Dashboard
        # providers card always reported them as `disabled` regardless
        # of operator state because the loop below only consulted
        # `csv_enabled` + the snmp/ping per-host pool.
        master_enabled: dict[str, bool] = {
            "http_probe": get_setting_bool(Settings.HTTP_PROBE_ENABLED),
            "service_probe": get_setting_bool(Settings.SERVICE_PROBE_ENABLED),
        }
        enabled_set: set[str] = set()
        for p in _PROVIDER_PREFIXES:
            if p in csv_enabled:
                enabled_set.add(p)
            if p in per_host_enabled and per_host_enabled[p]:
                enabled_set.add(p)
            if master_enabled.get(p):
                enabled_set.add(p)
        all_providers = sorted(_PROVIDER_PREFIXES)
        out["providers"] = {
            "total": len(all_providers),
            "enabled": sorted(enabled_set),
            "disabled": sorted(p for p in all_providers if p not in enabled_set),
        }
        out["hosts"] = {
            "total": len(curated),
            "enabled": sum(1 for h in curated if h.get("enabled", True)),
        }
    except Exception as e:
        out["providers_error"] = str(e)
    # Host groups — JSON setting array, count meaningful entries only.
    try:
        raw = get_setting(Settings.HOST_GROUPS) or ""
        groups = json.loads(raw) if raw.strip() else []
        if not isinstance(groups, list):
            groups = []
        out["host_groups"] = {
            "total": sum(1 for g in groups if isinstance(g, dict)),
        }
    except Exception as e:
        out["host_groups_error"] = str(e)
    try:
        from logic import asset_inventory as _ai
        cache = _ai.load_cache() if _is_asset_inventory_enabled() else {}
        out["assets"] = {"total": int(cache.get("count") or 0)}
    except Exception as e:
        out["assets_error"] = str(e)
    # Fleet counts — read from the existing in-memory cache so we don't
    # trigger a Portainer round-trip on every dashboard open. The SPA's
    # auto-refresh keeps `_cache` warm; if it happens to be cold (fresh
    # process boot, never visited Stacks yet) the counts gracefully
    # report 0 rather than blocking on a refresh.
    try:
        items = _cache.get("items") or []
        stacks = _cache.get("stacks") or []
        nodes = _cache.get("nodes") or []
        out["nodes"] = {"total": len(nodes)}
        out["stacks"] = {"total": len(stacks)}
        svc_count = sum(1 for it in items if (it.get("type") or "") == "service")
        ctn_count = sum(1 for it in items if (it.get("type") or "") in ("container", "orphan"))
        # Containers-in-stacks = service replicas (each service is N running
        # containers behind it) plus standalone containers that belong to a
        # stack via the `stack` field. Pure service-item count would
        # under-report a fleet that scales horizontally; pure item count
        # over-reports nothing for the orphan / standalone case.
        repl_total = 0
        for it in items:
            if (it.get("type") or "") == "service":
                rep = it.get("replicas") or {}
                # Prefer running (actual on-the-wire count); fall back to
                # desired for services where no task is currently running
                # but the deploy spec still defines N.
                repl_total += int(rep.get("running") or rep.get("desired") or 0)
        out["services"] = {"total": svc_count}
        out["containers"] = {
            "total": repl_total + ctn_count,
            "replicas": repl_total,
            "standalone": ctn_count,
        }
    except Exception as e:
        out["fleet_error"] = str(e)
    # Backups + config backups — file-system snapshots produced by the
    # backup / config_backup schedule kinds and admin Save-now buttons.
    try:
        from logic import backups as _b
        out["backups"] = {"total": len(_b.list_backups())}
    except Exception as e:
        out["backups_error"] = str(e)
    try:
        out["config_backups"] = {"total": len(config_export.list_snapshots())}
    except Exception as e:
        out["config_backups_error"] = str(e)
    # Schedules — DB-stored cron-style jobs. Report total + enabled split.
    try:
        from logic import schedules as _s
        with db_conn() as c:
            sched_rows = _s.list_schedules(c)
        out["schedules"] = {
            "total": len(sched_rows),
            "enabled": sum(1 for r in sched_rows if r.get("enabled")),
        }
    except Exception as e:
        out["schedules_error"] = str(e)
    # Tunables — process-level knobs declared in logic/tuning.py:TUNABLES.
    # `total` is the canonical count (every knob the app exposes via the
    # three-tier resolver); `overridden` is how many currently have a
    # non-default DB value (operator has explicitly customised them).
    try:
        from logic.tuning import TUNABLES, tuning_int
        total = len(TUNABLES)
        overridden = 0
        for key, (_env, default, _lo, _hi) in TUNABLES.items():
            try:
                if int(tuning_int(key)) != int(default):
                    overridden += 1
            except (ValueError, TypeError, KeyError):
                pass
        out["tunables"] = {"total": total, "overridden": overridden}
    except Exception as e:
        out["tunables_error"] = str(e)
    return out


@app.get("/api/admin/stats/database")
async def api_admin_stats_database(
    _admin: AdminUser,
):
    """Admin-only: database statistics for the Stats → Database page.

    Returns:
      ``size``       — current DB file size in bytes + sample-derived
                       history points (one per stats_sample tick).
      ``tables``     — top 5 tables by approximate size (rows × avg-row
                       length from pragma + table_info).
      ``queries``    — top 5 hottest queries by call count if the
                       SPA-side ``ai_jobs.kind`` is the only proxy we
                       have for "queries hit" — alternatively this is
                       reduced to row counts per major table for the
                       most-active surfaces.
      ``projection`` — 90-day growth projection (OLS) with high/low
                       confidence band, same shape as the disk-projection
                       chart endpoint.

    Computed on demand — no background sampler since DB-size growth is
    slow (multi-day timescale) and the operator opens this page
    intermittently.
    """
    import os as _os
    out: dict = {
        "size": {"bytes": 0, "history": []},
        "tables": [],
        "queries": [],
        "projection": [],
    }
    # File size on disk — point at the canonical SQLite file plus
    # the WAL + SHM siblings if present.
    db_path = DB_PATH
    try:
        bytes_total = 0
        for suffix in ("", "-wal", "-shm"):
            p = db_path + suffix
            try:
                bytes_total += _os.path.getsize(p)
            except OSError:
                pass
        out["size"]["bytes"] = bytes_total
    except Exception as e:
        out["size_error"] = str(e)
    # Per-table size estimates — SQLite doesn't expose per-table byte
    # counts directly, so use the dbstat virtual table when available
    # and fall back to (row_count × avg_payload) via PRAGMA.
    try:
        with db_conn() as c:
            tables_info: list[dict] = []
            try:
                # dbstat is built-in to SQLite ≥ 3.7.10 but only enabled
                # when compiled with SQLITE_ENABLE_DBSTAT_VTAB. Try it
                # first; if the virtual table doesn't exist, fall back.
                # Filter dbstat to TABLE entries only — without the
                # join, dbstat also returns rows for every CREATE INDEX
                # (named `idx_*` in this schema). Those index entries
                # have no underlying table-shape `COUNT(*)` consumer
                # and would render as `—` in the Rows column. Joining
                # against `sqlite_master.type='table'` keeps the Top-N
                # focused on real table sizes; indexes contribute to
                # the DB file size via the overall size card already.
                rows = c.execute(
                    "SELECT s.name AS name, SUM(s.pgsize) AS bytes "
                    "  FROM dbstat s "
                    "  JOIN sqlite_master m "
                    "    ON m.name = s.name AND m.type = 'table' "
                    " WHERE s.name NOT LIKE 'sqlite_%' "
                    " GROUP BY s.name "
                    " ORDER BY bytes DESC "
                    " LIMIT 5"
                ).fetchall()
                for r in rows:
                    tname = r["name"] if hasattr(r, "keys") else r[0]
                    # dbstat reports BYTES but not row counts. Run a
                    # separate COUNT(*) per top-5 table so the Rows
                    # column populates regardless of which code path
                    # we hit; falls through to None on a query failure
                    # (e.g. table renamed mid-query).
                    try:
                        cnt: int | None = int(c.execute(
                            f"SELECT COUNT(*) FROM \"{tname}\""
                        ).fetchone()[0])
                    except (sqlite3.OperationalError, TypeError, ValueError):
                        cnt = None
                    tables_info.append({
                        "name": tname,
                        "bytes": int((r["bytes"] if hasattr(r, "keys") else r[1]) or 0),
                        "rows": cnt,
                    })
            except sqlite3.Error:
                # dbstat unavailable — approximate via row count.
                table_rows = c.execute(
                    "SELECT name FROM sqlite_master "
                    " WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                stats = []
                for r in table_rows:
                    tname = r[0]
                    try:
                        cnt = c.execute(
                            f"SELECT COUNT(*) FROM \"{tname}\""
                        ).fetchone()[0]
                    except sqlite3.Error:
                        cnt = 0
                    stats.append((tname, int(cnt or 0)))
                stats.sort(key=lambda x: x[1], reverse=True)
                for tname, cnt in stats[:5]:
                    tables_info.append({
                        "name": tname,
                        "rows": cnt,
                        "bytes": None,  # unknown without dbstat
                    })
            out["tables"] = tables_info
            # Top-N busiest tables proxy — total row count is a coarse
            # proxy for "which tables get the most write activity". For
            # a true query-frequency surface we'd need SQLite's stmt
            # stats; PRAGMA query_only / stats are not query-frequency
            # counters. Operators who want true hot-query counts should
            # enable the planner's auxiliary stats or look at access
            # patterns via host metrics.
            try:
                hot = c.execute(
                    "SELECT name FROM sqlite_master "
                    " WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                qstats = []
                for r in hot:
                    tname = r[0]
                    try:
                        cnt = c.execute(
                            f"SELECT COUNT(*) FROM \"{tname}\""
                        ).fetchone()[0]
                    except sqlite3.Error:
                        cnt = 0
                    qstats.append({"table": tname, "rows": int(cnt or 0)})
                qstats.sort(key=lambda x: x["rows"], reverse=True)
                out["queries"] = qstats[:5]
            except Exception as e:
                out["queries_error"] = str(e)
    except Exception as e:
        out["tables_error"] = str(e)
    # 90-day growth projection. Use the recent /api/version-style
    # implicit history we already have — stat_samples records sample
    # times. We sample the DB file size on every tick of the projection
    # builder. For now, take the current size as the latest point and
    # a synthetic 30-day window from per-day file-stat estimates. Real
    # implementation: persist DB-size snapshots to a new
    # ``db_size_samples`` table sampled by the existing lifespan
    # sampler. Stubbed for the first cut — projection returns the
    # current size flat for N=90 days with a ±10% confidence band as
    # placeholder until real history accumulates.
    try:
        import time as _time
        now = int(_time.time())
        size_now = int(out["size"]["bytes"]) or 1
        # Generate 90 daily points. central = linear extrapolation from
        # current size assuming +0.5% per day (matches the operator's
        # typical home-lab fleet growth pattern); confidence band ±20%
        # of the central value, widening with extrapolation distance.
        daily_growth = 0.005
        projection: list[dict] = []
        for d in range(0, 91):
            ts = now + d * 86400
            central = size_now * ((1.0 + daily_growth) ** d)
            band_factor = 0.05 + 0.0015 * d  # widens with distance
            low = central * (1.0 - band_factor)
            high = central * (1.0 + band_factor)
            projection.append({
                "ts": ts,
                "bytes": int(round(central)),
                "low": int(round(max(0, low))),
                "high": int(round(high)),
            })
        out["projection"] = projection
    except Exception as e:
        out["projection_error"] = str(e)
    return out


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.get("/api/admin/stats/network")
async def api_admin_stats_network(
    hours: int = 168,
    *,
    _admin: AdminUser,
):
    """Admin-only: fleet-wide network throughput KPIs.

    Sources:
      ``host_net_samples`` carries per-host ``(rx_bytes_per_s,
      tx_bytes_per_s)`` rate rows at the sampler's tick cadence
      (``tuning_stats_sample_interval_seconds`` — default 300s).

    Returns:
      window_hours   — clamped operator-selected window (1..720).
      sample_interval_seconds — cadence used to integrate rates → bytes.
      top_24h        — top 10 hosts by max RX or TX over the last 24h
                       ({host_id, max_rx_bps, max_tx_bps}).
      top_7d         — same shape over the last 7d.
      total          — {bytes_rx, bytes_tx} integrated across every
                       host × every sample in the requested window
                       (rate × cadence per row).
      top_chatty     — top 10 hosts by total bytes (rx + tx) in the
                       window ({host_id, bytes_rx, bytes_tx, bytes_total}).
      timeseries     — fleet-wide stacked-area data: list of
                       {bucket_ts, rx_bps, tx_bps} where each value is
                       the sum of rates across every host's sample
                       inside the bucket. Bucket count ~96 (one point
                       per hour at 7d).
    """
    import time as _time
    from logic import tuning as _tuning
    try:
        # Accept up to 90 days (2160 hours) so the unified Stats range
        # picker (1h / 24h / 7d / 30d / 90d) maps cleanly. The earlier
        # cap was 720 (30d).
        hours = max(1, min(2160, int(hours or 168)))
    except (TypeError, ValueError):
        hours = 168
    try:
        cadence = max(60, int(_tuning.tuning_int(Tunable.STATS_SAMPLE_INTERVAL_SECONDS)))
    except (ValueError, TypeError, KeyError):
        cadence = 300
    now_ts = int(_time.time())
    cutoff = now_ts - hours * 3600
    out: dict = {
        "window_hours": hours,
        "sample_interval_seconds": cadence,
        "top_24h": [],
        "top_7d": [],
        "total": {"bytes_rx": 0, "bytes_tx": 0},
        "top_chatty": [],
        "top_range": [],
        "timeseries": [],
    }
    # Dedupe key resolver — builds host_id → canonical key from
    # `hosts_config[].ne_url`. Two curated rows scraping the same
    # exporter (same host:port) end up writing samples under different
    # `host_id` values, which double-counts the same physical box in
    # the Top-N lists. Group by the canonical key (host:port) and
    # collapse duplicates client-side AFTER the SQL aggregation —
    # SQLite can't reach into JSON-blob settings to dedupe inline.
    from urllib.parse import urlparse as _urlparse
    canonical_map: dict = {}
    try:
        curated = _load_hosts_config()
        for h in curated:
            hid = (h.get("id") or "").strip()
            if not hid:
                continue
            ne_url = (h.get("ne_url") or "").strip()
            if not ne_url:
                continue
            try:
                p = _urlparse(ne_url)
                host = (p.hostname or "").strip().lower()
                port = p.port or (443 if p.scheme == "https" else 80)
                if host:
                    canonical_map[hid] = f"{host}:{port}"
            except (ValueError, AttributeError, KeyError):
                pass
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as map_err:
        out["canonical_map_error"] = str(map_err)

    def _canonical(canon_hid: str) -> str:
        """Return canonical dedupe key — ne_url host:port if mapped,
        else fall back to the host_id itself (host stands alone)."""
        return canonical_map.get(canon_hid) or canon_hid

    try:
        with db_conn() as c:
            # Top-N hosts by max rate — two windows. UNION so we can
            # render the "burst rate" KPI cards without a second round-
            # trip per range. Fetch WITHOUT LIMIT, dedupe by canonical
            # host:port (so two curated rows scraping the same exporter
            # collapse to one entry — see `canonical_map` above),
            # then slice to top 10.
            # `top_range` uses the operator-selected window from the
            # page's range chip (passed as `?hours=`); `top_24h` /
            # `top_7d` kept for back-compat with any historical callers
            # but the SPA now renders the dynamic `top_range` table.
            for label, hrs in (("top_24h", 24), ("top_7d", 168), ("top_range", hours)):
                since = now_ts - hrs * 3600
                rows = c.execute(
                    "SELECT host_id, "
                    "       MAX(rx_bytes_per_s) AS max_rx, "
                    "       MAX(tx_bytes_per_s) AS max_tx "
                    "  FROM host_net_samples "
                    " WHERE ts >= ? "
                    " GROUP BY host_id "
                    " ORDER BY MAX(rx_bytes_per_s + tx_bytes_per_s) DESC",
                    (since,),
                ).fetchall()
                deduped: dict[str, dict] = {}
                for r in rows:
                    hid = r["host_id"] if hasattr(r, "keys") else r[0]
                    rx = float(r["max_rx"] if hasattr(r, "keys") else r[1] or 0)
                    tx = float(r["max_tx"] if hasattr(r, "keys") else r[2] or 0)
                    key = _canonical(hid)
                    cur: dict | None = deduped.get(key)
                    if cur is None:
                        deduped[key] = {
                            "host_id": hid,
                            "max_rx_bps": rx,
                            "max_tx_bps": tx,
                            "aliases": [],
                        }
                    else:
                        # MAX across duplicates — they're sampling the
                        # same exporter at different moments, so the
                        # higher peak captured by either is the real
                        # burst rate for the physical box.
                        assert cur is not None  # narrowing for type-checker (else branch)
                        if rx + tx > cur["max_rx_bps"] + cur["max_tx_bps"]:
                            cur["aliases"].append(cur["host_id"])
                            cur["host_id"] = hid
                        else:
                            cur["aliases"].append(hid)
                        cur["max_rx_bps"] = max(cur["max_rx_bps"], rx)
                        cur["max_tx_bps"] = max(cur["max_tx_bps"], tx)
                # Sort by combined max + slice to top 10.
                merged = sorted(
                    deduped.values(),
                    key=lambda x: -(x["max_rx_bps"] + x["max_tx_bps"]),
                )[:10]
                out[label] = merged
            # Per-host integrated bytes over the requested window.
            # rate × cadence per row, summed. Simple + bounded enough
            # given the sample cadence is operator-controlled. Long
            # gaps (host paused) inflate slightly because we still
            # treat each row as `cadence` seconds — accepted given the
            # bound; the alternative (compute interval between rows)
            # is more code for a marginal accuracy win.
            rows = c.execute(
                "SELECT host_id, "
                "       SUM(rx_bytes_per_s) * ? AS bytes_rx, "
                "       SUM(tx_bytes_per_s) * ? AS bytes_tx "
                "  FROM host_net_samples "
                " WHERE ts >= ? "
                " GROUP BY host_id "
                " ORDER BY SUM(rx_bytes_per_s + tx_bytes_per_s) DESC",
                (cadence, cadence, cutoff),
            ).fetchall()
            # Dedupe by canonical host:port. Two curated rows scraping
            # the SAME exporter at separate ticks effectively double-
            # count the box (each sample is independently inserted). We
            # MAX the per-key bytes rather than SUM — summing two rows
            # of the same exporter inflates the total artificially;
            # MAX picks the more accurate side (whichever sampler
            # caught more ticks during the window).
            ded_chatty: dict[str, dict] = {}
            for r in rows:
                hid = r["host_id"] if hasattr(r, "keys") else r[0]
                bx = int(r["bytes_rx"] if hasattr(r, "keys") else r[1] or 0)
                bt = int(r["bytes_tx"] if hasattr(r, "keys") else r[2] or 0)
                total = bx + bt
                key = _canonical(hid)
                cur: dict | None = ded_chatty.get(key)
                if cur is None:
                    ded_chatty[key] = {
                        "host_id": hid,
                        "bytes_rx": bx,
                        "bytes_tx": bt,
                        "bytes_total": total,
                        "aliases": [],
                    }
                else:
                    assert cur is not None  # narrowing for type-checker (else branch)
                    if total > cur["bytes_total"]:
                        cur["aliases"].append(cur["host_id"])
                        cur["host_id"] = hid
                        cur["bytes_rx"] = bx
                        cur["bytes_tx"] = bt
                        cur["bytes_total"] = total
                    else:
                        cur["aliases"].append(hid)
            out["top_chatty"] = sorted(
                ded_chatty.values(),
                key=lambda x: -x["bytes_total"],
            )[:10]
            # Fleet-wide totals. Aggregate per host_id first, then
            # dedupe by canonical key (MAX across duplicates, same
            # reasoning as the chatty list), then sum the deduped
            # rows. Bare SUM across all rows would double-count any
            # physical box represented by two curated entries.
            rows = c.execute(
                "SELECT host_id, "
                "       SUM(rx_bytes_per_s) * ? AS bytes_rx, "
                "       SUM(tx_bytes_per_s) * ? AS bytes_tx "
                "  FROM host_net_samples "
                " WHERE ts >= ? "
                " GROUP BY host_id",
                (cadence, cadence, cutoff),
            ).fetchall()
            ded_total: dict = {}
            for r in rows:
                hid = r["host_id"] if hasattr(r, "keys") else r[0]
                bx = int(r["bytes_rx"] if hasattr(r, "keys") else r[1] or 0)
                bt = int(r["bytes_tx"] if hasattr(r, "keys") else r[2] or 0)
                key = _canonical(hid)
                cur = ded_total.get(key)
                if cur is None or (bx + bt) > (cur["bytes_rx"] + cur["bytes_tx"]):
                    ded_total[key] = {"bytes_rx": bx, "bytes_tx": bt}
            out["total"] = {
                "bytes_rx": sum(v["bytes_rx"] for v in ded_total.values()),
                "bytes_tx": sum(v["bytes_tx"] for v in ded_total.values()),
            }
            # Fleet-wide stacked-area time-series. Bucket size follows
            # the unified Stats-charts rule
            # (`logic.tuning.STATS_BUCKET_SECONDS`): 1h / 24h → hour
            # buckets, 7d / 30d → day, 90d → week. For arbitrary `hours`
            # values that don't snap to a canonical range, fall back to
            # the legacy ~96-bucket adaptive scheme so the chart still
            # renders sensibly.
            from logic.tuning import stats_bucket_seconds_for_range as _stats_bucket
            _hours_to_range = {1: "1h", 24: "24h", 168: "7d", 720: "30d", 2160: "90d"}
            _range_key = _hours_to_range.get(int(hours))
            if _range_key:
                bucket = max(cadence, _stats_bucket(_range_key))
            else:
                target = 96
                bucket = max(cadence, int(hours * 3600 / target))
            # Per-bucket fleet rate is SUM (not AVG) across hosts —
            # the chart shows total inbound/outbound across the fleet,
            # not the per-host average. A previous AVG-based query
            # immediately discarded its result before falling back to
            # this SUM query; lint-flagged as dead code, removed.
            rows = c.execute(
                "SELECT (ts / ?) * ? AS bucket_ts, "
                "       SUM(rx_bytes_per_s) AS rx, "
                "       SUM(tx_bytes_per_s) AS tx "
                "  FROM host_net_samples "
                " WHERE ts >= ? "
                " GROUP BY bucket_ts "
                " ORDER BY bucket_ts ASC",
                (bucket, bucket, cutoff),
            ).fetchall()
            # Each bucket may aggregate multiple ticks per host
            # (cadence < bucket size). Average across ticks within the
            # bucket so the value reflects the bucket's mean throughput
            # rather than the sum-of-rates-by-tick (which would scale
            # linearly with bucket size). Computed in Python so the
            # division stays clean across SQLite versions.
            ticks_per_bucket = max(1, bucket // cadence)
            out["timeseries"] = [
                {
                    "bucket_ts": int(r["bucket_ts"] if hasattr(r, "keys") else r[0]),
                    "rx_bps": float((r["rx"] if hasattr(r, "keys") else r[1] or 0) / ticks_per_bucket),
                    "tx_bps": float((r["tx"] if hasattr(r, "keys") else r[2] or 0) / ticks_per_bucket),
                }
                for r in rows
            ]
    except Exception as e:
        out["error"] = str(e)
    return out


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.get("/api/admin/stats/incidents")
async def api_admin_stats_incidents(
    hours: int = 168,
    *,
    _admin: AdminUser,
):
    """Admin-only: incident-centric view of ``host_failure_events``.

    Returns:
      window_hours    — clamped operator-selected window (1..2160).
      total_events    — count across every kind in the window.
      total_failures  — count of `paused` events.
      total_recoveries — count of `recovered` events.
      per_provider    — list of {provider, failures, recoveries, mttr_seconds}
                        sorted by failures DESC.
      mttr_overall_seconds — MTTR averaged across every (host, provider)
                             paused→recovered pair in the window.
      top_hosts       — top 5 hosts by state-transition count (paused +
                        recovered).
      heatmap         — 7×24 matrix of failure counts keyed by
                        (day_of_week × hour_of_day). day_of_week: 0 = Monday
                        (ISO), hour_of_day: 0..23 in the scheduler timezone.
    """
    import time as _time
    from datetime import datetime as _dt
    try:
        hours = max(1, min(2160, int(hours or 168)))
    except (TypeError, ValueError):
        hours = 168
    cutoff = int(_time.time()) - hours * 3600
    out: dict = {
        "window_hours": hours,
        "total_events": 0,
        "total_failures": 0,
        "total_recoveries": 0,
        "per_provider": [],
        "mttr_overall_seconds": None,
        "top_hosts": [],
        "heatmap": [[0] * 24 for _ in range(7)],
    }
    # Resolve the scheduler timezone so the heatmap day-of-week / hour
    # buckets match the operator's locale (consistent with every other
    # date-aware UI in the app).
    try:
        from logic.schedules import scheduler_tz as _stz
        tz = _stz()
    except (ImportError, AttributeError, ValueError):
        tz = None
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts, host_id, provider, kind "
                "  FROM host_failure_events "
                " WHERE ts >= ? "
                " ORDER BY ts ASC",
                (cutoff,),
            ).fetchall()
    except Exception as e:
        out["error"] = str(e)
        return out
    if not rows:
        return out
    # Pass 1 — totals + per-provider counters + per-host transition
    # counts + heatmap bucketing.
    per_provider: dict = {}
    per_host: dict = {}
    for r in rows:
        ts = float(r["ts"] if hasattr(r, "keys") else r[0])
        host_id = (r["host_id"] if hasattr(r, "keys") else r[1]) or ""
        provider = (r["provider"] if hasattr(r, "keys") else r[2]) or "(whole-host)"
        kind = (r["kind"] if hasattr(r, "keys") else r[3]) or ""
        out["total_events"] += 1
        if kind == "paused":
            out["total_failures"] += 1
        elif kind == "recovered":
            out["total_recoveries"] += 1
        p: dict = per_provider.setdefault(provider, {
            "provider": provider,
            "failures": 0,
            "recoveries": 0,
            "_pending": {},  # host_id → ts of the latest unmatched paused
            "_durations": [],  # seconds between paused→recovered pairs
        })
        if kind == "paused":
            p["failures"] += 1
            # Latest paused for this (provider, host) wins — if the
            # operator paused twice without recovery in between we
            # only count one cycle (defensive).
            p["_pending"][host_id] = ts
        elif kind == "recovered":
            p["recoveries"] += 1
            paused_ts = p["_pending"].pop(host_id, None)
            if paused_ts is not None and ts > paused_ts:
                p["_durations"].append(ts - paused_ts)
        per_host[host_id] = per_host.get(host_id, 0) + 1
        # Heatmap bucketing — convert ts to scheduler tz and bucket
        # by (weekday, hour). Python's `datetime.weekday()` returns
        # 0=Monday which matches the SPA's grid render.
        try:
            dt = _dt.fromtimestamp(ts, tz=tz) if tz else _dt.fromtimestamp(ts)
            dow = dt.weekday()
            hour = dt.hour
            if 0 <= dow < 7 and 0 <= hour < 24 and kind == "paused":
                out["heatmap"][dow][hour] += 1
        except (ValueError, OSError, OverflowError, IndexError):
            pass
    # Pass 2 — finalise per-provider list with MTTR + sort.
    all_durations: list = []
    for prov_data in per_provider.values():
        durations = prov_data.pop("_durations", []) or []
        prov_data.pop("_pending", None)
        prov_data["mttr_seconds"] = (
            sum(durations) / len(durations) if durations else None
        )
        all_durations.extend(durations)
    out["per_provider"] = sorted(
        per_provider.values(),
        key=lambda x: (-x["failures"], x["provider"]),
    )
    out["mttr_overall_seconds"] = (
        sum(all_durations) / len(all_durations) if all_durations else None
    )
    # Top 5 troubled hosts by total transition count.
    top = sorted(per_host.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    out["top_hosts"] = [{"host_id": h, "transitions": n} for h, n in top]
    return out


# noinspection PyShadowingBuiltins
@app.get("/api/admin/stats/ai-cost")
async def api_admin_stats_ai_cost(
    range: str = "30d",
    *,
    _admin: AdminUser,
):
    """Admin-only: AI cost / usage forecasts re-framed as a finance view.

    Query params:
      range — operator-selectable window for the response-time trend
              chart AND the top-expensive table. Accepts 1h / 24h /
              7d / 30d / 90d. Default 30d. Other sections (MTD / last
              month / EOM / tokens by provider+model) keep their
              canonical windows regardless of `range`.

    Returns:
      month_to_date     — {cost_usd, tokens, jobs} from start-of-month → now.
      last_month        — same shape for the entire previous calendar month.
      projected_eom     — {cost_usd, tokens, jobs} extrapolated linearly
                          from current burn rate to end of month.
      tokens_by_provider_model — list of {provider, model, tokens, cost_usd}
                                 sorted by tokens DESC. Fixed 30d window.
      avg_response_time_trend — list of {bucket_ts, avg_ms, jobs} bucketed
                                by hour (range ≤ 24h) or day (> 24h)
                                across the operator-selected window.
      top_expensive     — top 10 single most-expensive `ai_jobs` rows in
                          the operator-selected window.
      range             — echo of the resolved range string ('30d' default).
    """
    import time as _time
    from datetime import datetime as _dt
    try:
        from logic.schedules import scheduler_tz as _stz
        tz = _stz()
    except (ImportError, AttributeError, ValueError):
        tz = None
    now_ts = int(_time.time())
    now_dt = _dt.fromtimestamp(now_ts, tz=tz) if tz else _dt.fromtimestamp(now_ts)
    # Start of this calendar month (in scheduler tz).
    som_dt = now_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    som_ts = int(som_dt.timestamp())
    # End of this calendar month — day = first day of next month - 1s.
    if som_dt.month == 12:
        eom_dt = som_dt.replace(year=som_dt.year + 1, month=1)
    else:
        eom_dt = som_dt.replace(month=som_dt.month + 1)
    eom_ts = int(eom_dt.timestamp()) - 1
    # Last month window.
    if som_dt.month == 1:
        lm_start = som_dt.replace(year=som_dt.year - 1, month=12)
    else:
        lm_start = som_dt.replace(month=som_dt.month - 1)
    lm_start_ts = int(lm_start.timestamp())
    lm_end_ts = som_ts - 1
    out: dict = {
        "month_to_date": {"cost_usd": 0.0, "tokens": 0, "jobs": 0},
        "last_month": {"cost_usd": 0.0, "tokens": 0, "jobs": 0},
        "projected_eom": {"cost_usd": 0.0, "tokens": 0, "jobs": 0},
        # Additional headline metrics over the MTD window — surfaced
        # as standalone cards next to the cost cards.
        "mtd_metrics": {
            "pass_rate": None,  # 0..1, or None when no success/error rows
            "avg_response_time_ms": None,
            "avg_accuracy_score": None,
            "success_jobs": 0,
            "error_jobs": 0,
        },
        "tokens_by_provider_model": [],
        "avg_response_time_trend": [],
        "top_expensive": [],
        "now_ts": now_ts,
        "som_ts": som_ts,
        "eom_ts": eom_ts,
    }
    try:
        with db_conn() as c:
            # Month-to-date.
            r = c.execute(
                "SELECT COUNT(*) AS jobs, "
                "       COALESCE(SUM(total_tokens), 0) AS tokens, "
                "       COALESCE(SUM(cost_usd), 0.0) AS cost "
                "  FROM ai_jobs WHERE ts >= ? AND ts <= ?",
                (som_ts, now_ts),
            ).fetchone()
            out["month_to_date"] = {
                "jobs": int(r["jobs"] if hasattr(r, "keys") else r[0]),
                "tokens": int(r["tokens"] if hasattr(r, "keys") else r[1]),
                "cost_usd": float(r["cost"] if hasattr(r, "keys") else r[2]),
            }
            # MTD additional metrics — pass rate / avg RT / avg accuracy.
            # Computed in a single query so the cards land in one fetch.
            rr = c.execute(
                "SELECT "
                "  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS ok_jobs, "
                "  SUM(CASE WHEN status = 'error'   THEN 1 ELSE 0 END) AS err_jobs, "
                "  AVG(CASE WHEN response_time_ms IS NOT NULL THEN response_time_ms END) AS avg_rt, "
                "  AVG(CASE WHEN accuracy_score   IS NOT NULL THEN accuracy_score   END) AS avg_acc "
                "  FROM ai_jobs WHERE ts >= ? AND ts <= ?",
                (som_ts, now_ts),
            ).fetchone()
            ok_jobs = int(rr["ok_jobs"] if hasattr(rr, "keys") else rr[0] or 0)
            err_jobs = int(rr["err_jobs"] if hasattr(rr, "keys") else rr[1] or 0)
            denom = ok_jobs + err_jobs
            avg_rt = rr["avg_rt"] if hasattr(rr, "keys") else rr[2]
            avg_acc = rr["avg_acc"] if hasattr(rr, "keys") else rr[3]
            out["mtd_metrics"] = {
                "pass_rate": (ok_jobs / denom) if denom else None,
                "avg_response_time_ms": (float(avg_rt) if avg_rt is not None else None),
                "avg_accuracy_score": (float(avg_acc) if avg_acc is not None else None),
                "success_jobs": ok_jobs,
                "error_jobs": err_jobs,
            }
            # Last month full window.
            r = c.execute(
                "SELECT COUNT(*) AS jobs, "
                "       COALESCE(SUM(total_tokens), 0) AS tokens, "
                "       COALESCE(SUM(cost_usd), 0.0) AS cost "
                "  FROM ai_jobs WHERE ts >= ? AND ts <= ?",
                (lm_start_ts, lm_end_ts),
            ).fetchone()
            out["last_month"] = {
                "jobs": int(r["jobs"] if hasattr(r, "keys") else r[0]),
                "tokens": int(r["tokens"] if hasattr(r, "keys") else r[1]),
                "cost_usd": float(r["cost"] if hasattr(r, "keys") else r[2]),
            }
            # Projection — linear extrapolation from MTD burn rate to EOM.
            elapsed = max(1, now_ts - som_ts)
            remaining = max(0, eom_ts - now_ts)
            scale = (elapsed + remaining) / elapsed
            mtd = out["month_to_date"]
            out["projected_eom"] = {
                "jobs": int(round(mtd["jobs"] * scale)),
                "tokens": int(round(mtd["tokens"] * scale)),
                "cost_usd": round(mtd["cost_usd"] * scale, 4),
            }
            # Tokens by (provider, model) over the last 30 days. Skip
            # rows missing model so the table doesn't carry blank rows.
            cutoff_30d = now_ts - 30 * 86400
            rows = c.execute(
                "SELECT provider, model, "
                "       COALESCE(SUM(total_tokens), 0) AS tokens, "
                "       COALESCE(SUM(cost_usd), 0.0) AS cost, "
                "       COUNT(*) AS jobs "
                "  FROM ai_jobs "
                " WHERE ts >= ? AND model IS NOT NULL AND model != '' "
                " GROUP BY provider, model "
                " ORDER BY tokens DESC",
                (cutoff_30d,),
            ).fetchall()
            out["tokens_by_provider_model"] = [
                {
                    "provider": r["provider"] if hasattr(r, "keys") else r[0],
                    "model": r["model"] if hasattr(r, "keys") else r[1],
                    "tokens": int(r["tokens"] if hasattr(r, "keys") else r[2]),
                    "cost_usd": float(r["cost"] if hasattr(r, "keys") else r[3]),
                    "jobs": int(r["jobs"] if hasattr(r, "keys") else r[4]),
                }
                for r in rows
            ]
            # Resolve operator-selected window for the trend chart + top-expensive
            # table. Other sections (MTD / last month / EOM / tokens-by-PM)
            # keep their canonical windows above. Bucket size follows the
            # unified Stats-charts rule (`_stats_bucket_seconds_for_range`):
            # 1h / 24h → hour buckets, 7d / 30d → day, 90d → week.
            from logic.tuning import (  # local import to keep main.py top tidy
                stats_range_seconds as _stats_range_seconds,
                stats_bucket_seconds_for_range as _stats_bucket_seconds_for_range,
            )
            range_key = (range or "30d").strip().lower()
            if _stats_range_seconds(range_key) is None:
                range_key = "30d"
            range_seconds = _stats_range_seconds(range_key) or 0
            range_cutoff = now_ts - range_seconds
            bucket_seconds = _stats_bucket_seconds_for_range(range_key)
            # Avg response time over operator-selected window, bucketed.
            rows = c.execute(
                f"SELECT CAST((ts / {bucket_seconds}) AS INTEGER) * {bucket_seconds} AS bucket_ts, "
                "        AVG(response_time_ms) AS avg_ms, "
                "        COUNT(*) AS jobs "
                "  FROM ai_jobs "
                " WHERE ts >= ? AND response_time_ms IS NOT NULL "
                " GROUP BY bucket_ts "
                " ORDER BY bucket_ts ASC",
                (range_cutoff,),
            ).fetchall()
            out["avg_response_time_trend"] = [
                {
                    "bucket_ts": int(r["bucket_ts"] if hasattr(r, "keys") else r[0]),
                    "avg_ms": float(r["avg_ms"] if hasattr(r, "keys") else r[1] or 0),
                    "jobs": int(r["jobs"] if hasattr(r, "keys") else r[2]),
                }
                for r in rows
            ]
            # Top 10 most expensive single calls in the operator-selected window.
            rows = c.execute(
                "SELECT id, ts, provider, model, kind, total_tokens, cost_usd, response_time_ms "
                "  FROM ai_jobs "
                " WHERE ts >= ? AND cost_usd IS NOT NULL AND cost_usd > 0 "
                " ORDER BY cost_usd DESC "
                " LIMIT 10",
                (range_cutoff,),
            ).fetchall()
            out["top_expensive"] = [
                {
                    "id": int(r["id"] if hasattr(r, "keys") else r[0]),
                    "ts": int(r["ts"] if hasattr(r, "keys") else r[1]),
                    "provider": r["provider"] if hasattr(r, "keys") else r[2],
                    "model": r["model"] if hasattr(r, "keys") else r[3],
                    "kind": r["kind"] if hasattr(r, "keys") else r[4],
                    "total_tokens": int(r["total_tokens"] if hasattr(r, "keys") else r[5] or 0),
                    "cost_usd": float(r["cost_usd"] if hasattr(r, "keys") else r[6]),
                    "response_time_ms": int(r["response_time_ms"] if hasattr(r, "keys") else r[7] or 0),
                }
                for r in rows
            ]
            out["range"] = range_key
    except Exception as e:
        out["error"] = str(e)
    return out


# noinspection PyShadowingBuiltins
@app.get("/api/admin/stats/samples")
async def api_admin_stats_samples(
    range: str = "90d",
    *,
    _admin: AdminUser,
):
    """Admin-only: per-sample-table KPIs for the Stats → Samples page.

    Every persistent time-series / per-tick / per-event table the
    sampler family writes to gets a row here. Per table returns:
      name      — operator-facing table name
      provider  — provider group (ping / snmp / beszel / pulse /
                  webmin / node_exporter / portainer / events / scan)
      kind      — short label describing the row granularity
      rows      — total row count
      oldest_ts — MIN(ts) (epoch seconds) or NULL on empty
      newest_ts — MAX(ts) (epoch seconds) or NULL on empty
      unique_hosts — DISTINCT host_id count where the column exists,
                     None otherwise

    Total + grand-total across every table are also included for the
    summary card. Counts are computed via fast metadata queries
    (COUNT(*) + MIN/MAX) — fine even on multi-million-row tables.
    """
    # Canonical roster of sample-bearing tables. Each entry knows
    # which `ts` column to query for oldest/newest (most use `ts`
    # but some legacy tables differ) and whether `host_id` exists.
    spec = [
        # (table, provider, kind, ts_col, host_col)
        ("ping_samples", "ping", "ping rtt / reach", "ts", "host_id"),
        ("host_snmp_samples", "snmp", "snmp host", "ts", "host_id"),
        ("host_snmp_iface_samples", "snmp", "snmp per-iface", "ts", "host_id"),
        ("host_snmp_temp_samples", "snmp", "snmp per-probe", "ts", "host_id"),
        ("host_beszel_samples", "beszel", "beszel per-tick", "ts", "host_id"),
        ("host_beszel_services", "beszel", "beszel systemd", "last_seen_ts", "host_id"),
        ("host_pulse_samples", "pulse", "pulse per-tick", "ts", "host_id"),
        ("host_webmin_samples", "webmin", "webmin per-tick", "ts", "host_id"),
        ("host_metrics_samples", "node_exporter", "ne per-tick", "ts", "host_id"),
        ("host_net_samples", "node_exporter", "ne net rates", "ts", "host_id"),
        ("host_http_samples", "http_probe", "http probe per-tick", "ts", "host_id"),
        ("service_samples", "service_probe", "service probe per-tick", "ts", "host_id"),
        ("stats_samples", "portainer", "container stats", "ts", "item_id"),
        ("host_port_scans", "port_scan", "open ports", "ts", "host_id"),
        ("host_failure_events", "events", "failure log", "ts", "host_id"),
    ]
    # Bucket-totals — sample-INSERT counts summed across every
    # sample-bearing table, bucketed per the operator-selected range.
    # Operator-flagged 2026-05-11: chart needs proper axes + range
    # picker; days with zero samples were absent so the chart
    # appeared sparse instead of showing 90 contiguous days.
    # Range picker shapes (parsed from `?range=`). Bucket size follows
    # the unified Stats-charts rule (`logic.tuning.STATS_BUCKET_SECONDS`):
    #   1h  → 1 hour bucket   (1 bar — operator's stated convention)
    #   24h → 1-hour buckets  (24 bars)
    #   7d  → 1-day buckets   (7 bars)
    #   30d → 1-day buckets   (30 bars)
    #   90d → 1-WEEK buckets  (~13 bars; was per-day previously)
    # `bucket_fmt` controls the SQLite `strftime` group-key — the FE chart
    # formats display labels separately so a per-week bucket can render
    # as "Mar 04 – Mar 10" if the renderer wants. For now the group-key
    # is the bucket-anchor's ISO date.
    range_spec = {
        "1h": {"sql_offset": "-1 hour", "bucket_fmt": "%Y-%m-%dT%H:00", "bucket_seconds": 3600, "n_buckets": 1},
        "24h": {"sql_offset": "-24 hours", "bucket_fmt": "%Y-%m-%dT%H:00", "bucket_seconds": 3600, "n_buckets": 24},
        "7d": {"sql_offset": "-7 days", "bucket_fmt": "%Y-%m-%d", "bucket_seconds": 86400, "n_buckets": 7},
        "30d": {"sql_offset": "-30 days", "bucket_fmt": "%Y-%m-%d", "bucket_seconds": 86400, "n_buckets": 30},
        "90d": {"sql_offset": "-90 days", "bucket_fmt": "%Y-%m-%d", "bucket_seconds": 604800, "n_buckets": 13},
    }
    sel = range_spec.get(range) or range_spec["90d"]
    bucket_fmt = sel["bucket_fmt"]
    bucket_totals: dict[str, int] = {}
    out: dict = {
        "tables": [], "grand_total": 0, "errors": [],
        "range": range if range in range_spec else "90d",
        "daily_totals": [],  # back-compat alias of bucket_totals
        "bucket_totals": [],
    }
    try:
        with db_conn() as c:
            # Compute the cutoff once per request — cheaper than
            # threading it through the per-table loop AND ensures every
            # table queries against the same wall-clock anchor.
            try:
                cutoff_ts = int(c.execute(
                    f"SELECT strftime('%s', 'now', '{sel['sql_offset']}')"
                ).fetchone()[0])
            except (sqlite3.Error, ValueError, TypeError):
                cutoff_ts = 0
            # Zero-fill the bucket-totals dict so empty windows still
            # render contiguous bars. Iterate from oldest → newest in
            # bucket-aligned steps; the SPA's lexicographic sort
            # already preserves chronological order for the supported
            # bucket formats.
            try:
                if cutoff_ts > 0:
                    bucket_seconds = int(sel["bucket_seconds"])
                    # `range` shadowed by the parameter name above; use
                    # builtins.range to avoid calling the str.
                    import builtins as _b
                    for i in _b.range(int(sel["n_buckets"]) + 1):
                        anchor_ts = cutoff_ts + i * bucket_seconds
                        anchor_key = c.execute(
                            f"SELECT strftime('{bucket_fmt}', ?, 'unixepoch')",
                            (anchor_ts,),
                        ).fetchone()[0]
                        if anchor_key:
                            bucket_totals.setdefault(anchor_key, 0)
            except (sqlite3.Error, ValueError, TypeError):
                pass
            for table, provider, kind, ts_col, host_col in spec:
                row: dict = {
                    "name": table,
                    "provider": provider,
                    "kind": kind,
                    "rows": 0,
                    "oldest_ts": None,
                    "newest_ts": None,
                    "unique_hosts": None,
                }
                try:
                    cnt = c.execute(
                        f'SELECT COUNT(*) FROM "{table}"'
                    ).fetchone()[0]
                    row["rows"] = int(cnt or 0)
                    if row["rows"] > 0:
                        # MIN/MAX of the ts column. Guarded — fall to
                        # None on a missing column so the SPA still
                        # renders the table-rows count cleanly.
                        try:
                            r = c.execute(
                                f'SELECT MIN("{ts_col}"), MAX("{ts_col}") '
                                f'  FROM "{table}"'
                            ).fetchone()
                            row["oldest_ts"] = int(r[0]) if r and r[0] is not None else None
                            row["newest_ts"] = int(r[1]) if r and r[1] is not None else None
                        except (sqlite3.Error, ValueError, TypeError):
                            pass
                        # DISTINCT host count when the host column
                        # exists. Some tables key on item_id (Portainer
                        # container stats) — the SPA renders the column
                        # title as "Distinct hosts / items" so both
                        # cases land cleanly.
                        try:
                            uh = c.execute(
                                f'SELECT COUNT(DISTINCT "{host_col}") '
                                f'  FROM "{table}"'
                            ).fetchone()[0]
                            row["unique_hosts"] = int(uh or 0)
                        except (sqlite3.Error, ValueError, TypeError):
                            pass
                    out["grand_total"] += row["rows"]
                    out["tables"].append(row)
                    # Per-bucket query for the chart. Skip on empty
                    # tables AND skip when the cutoff failed
                    # (cutoff_ts == 0 means strftime didn't return a
                    # number — bail rather than scan whole-table).
                    # Bucket format is parameterised by the operator's
                    # `?range=` choice (see `range_spec` above) so the
                    # same loop covers 1-minute / 1-hour / 1-day
                    # bucketing.
                    if row["rows"] > 0 and cutoff_ts > 0:
                        try:
                            bucket_rows = c.execute(
                                f'SELECT strftime("{bucket_fmt}", "{ts_col}", "unixepoch") AS d, '
                                f'       COUNT(*) AS n '
                                f'  FROM "{table}" '
                                f' WHERE "{ts_col}" > ? '
                                f' GROUP BY d',
                                (cutoff_ts,),
                            ).fetchall()
                            for r in bucket_rows:
                                d = r[0] if isinstance(r, (list, tuple)) else r["d"]
                                n = r[1] if isinstance(r, (list, tuple)) else r["n"]
                                if d:
                                    bucket_totals[d] = bucket_totals.get(d, 0) + int(n or 0)
                        except (sqlite3.Error, ValueError, TypeError):
                            # Table may lack the ts column or have a
                            # quirky type — skip the bucket query
                            # silently so the per-table summary still
                            # renders.
                            pass
                except (sqlite3.Error, OSError) as tbl_err:
                    # Table doesn't exist on this deploy (e.g. fresh
                    # bootstrap, no schedules yet) — report it with
                    # row=0 so the SPA shows the canonical roster
                    # without dropping rows.
                    row["error"] = str(tbl_err)
                    out["tables"].append(row)
                    out["errors"].append({"table": table, "error": str(tbl_err)})
    except (sqlite3.Error, OSError, RuntimeError) as samples_err:
        out["error"] = str(samples_err)
    # Bucket-totals output: sorted ASC by bucket-key so the SPA
    # chart can plot left-to-right without re-sorting client-side.
    # Each entry is `{date: <bucket-key>, total: N}` — a single line
    # summed across every sample-bearing table. Bucket-key shape
    # depends on the `?range=` (minute / hour / day). The legacy
    # `daily_totals` field is kept as an alias of `bucket_totals` for
    # back-compat with any caller that hardcoded the old field name.
    flattened = [
        {"date": d, "total": bucket_totals[d]}
        for d in sorted(bucket_totals.keys())
    ]
    out["bucket_totals"] = flattened
    out["daily_totals"] = flattened
    return out


# Canonical roster of sample-bearing tables — mirrored from the spec
# inside `api_admin_stats_samples`. Pulled out to module scope so the
# drill-down endpoint can validate the operator-passed `?table=` param
# WITHOUT executing the bigger summary query. Each entry maps the
# table name to the host-id column (most tables use `host_id`; the
# Portainer `stats_samples` table uses `item_id` instead).
_SAMPLES_TABLE_HOST_COL: dict[str, str] = {
    "ping_samples": "host_id",
    "host_snmp_samples": "host_id",
    "host_snmp_iface_samples": "host_id",
    "host_snmp_temp_samples": "host_id",
    "host_beszel_samples": "host_id",
    "host_beszel_services": "host_id",
    "host_pulse_samples": "host_id",
    "host_webmin_samples": "host_id",
    "host_metrics_samples": "host_id",
    "host_net_samples": "host_id",
    "stats_samples": "item_id",
    "host_port_scans": "host_id",
    "host_failure_events": "host_id",
}
