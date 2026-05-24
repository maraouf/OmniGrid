"""Continuation of `main` — extracted to keep main.py under the
line-count "uncomfortable to navigate" threshold. Re-exported via
`from main_routes import *` at the bottom of `main.py`, which pulls
every public symbol (including FastAPI routes registered through
`@app.<verb>(...)`) back into the main namespace.

Loading order:
  1. main.py runs top-to-bottom, defining `app`, every helper,
     and roughly the first half of the routes.
  2. main.py end: `from main_routes import *` triggers main_routes load.
  3. main_routes.py top: `from main import *` pulls EVERY symbol
     main has defined so far (`app`, helpers, Pydantic models,
     etc.) into main_routes's namespace so the route decorators
     below can reference them.
  4. main_routes.py body runs; routes register against the shared
     `app` instance.
  5. main_routes.py finishes; control returns to main.py's star-
     import which now has every main_routes symbol available.
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

# Re-import parent's namespace so decorators below find `app`,
# helpers, Pydantic models, etc.
from main import *  # noqa: E402,F401,F403



def _failure_state_for_host(host_id: str) -> dict:
    """Read the host_failure_state row for a given host. Returns
    the four fields when the read succeeds AND the row exists. Returns
    only the falsy defaults when the row genuinely doesn't exist (host
    has never failed). Returns an EMPTY dict on any DB error so the
    spread in `_shape_host_api_row` becomes a no-op — letting the
    frontend's in-place reconcile preserve the previously-known values
    instead of momentarily flipping `sampling_paused` to false during
    a transient SQLite BUSY (which the frontend would render as the
    icon vanishing and reappearing on every poll cycle)."""
    try:
        with db_conn() as c:
            cur = c.execute(
                "SELECT first_failure_ts, consecutive_failures, paused, "
                "last_error, last_failure_ts, paused_at "
                "FROM host_failure_state WHERE host_id = ? AND provider = ''",
                (host_id,),
            )
            row = cur.fetchone()
    except (sqlite3.Error, OSError):
        # Don't return falsy defaults — that would clobber a previously
        # paused row's marker on the wire. Empty dict means "no info,
        # frontend keep what you had". See.
        return {}
    if row is None:
        # Row genuinely absent — host has never failed.
        return {
            "sampling_paused": False,
            "failure_window_started_at": 0,
            "consecutive_failures": 0,
            "last_error": "",
            "last_failure_ts": 0,
            "paused_at": 0,
        }
    # surface ``last_failure_ts`` so the drawer can render
    # "last error N seconds ago" alongside the existing
    # "first failure M minutes ago" banner copy. Falls back to
    # ``first_failure_ts`` for rows that pre-date the column add (the
    # first probe failure on the new schema overwrites the NULL).
    last_ts = row[4] if (len(row) > 4 and row[4] is not None) else row[0]
    # surface ``paused_at`` so the drawer can render
    # "auto-paused N hours ago". Pre-fix the SELECT omitted this
    # column even though the sampler writes it on every paused
    # transition. Same drift class as the ``last_failure_ts``
    # add — every additive ALTER TABLE means audit every SELECT
    # against that table (CLAUDE.md "SQL drift" rule).
    paused_at = row[5] if (len(row) > 5 and row[5] is not None) else 0
    return {
        "sampling_paused": bool(row[2]),
        "failure_window_started_at": int(row[0] or 0),
        "consecutive_failures": int(row[1] or 0),
        "last_error": row[3] or "",
        "last_failure_ts": int(last_ts or 0),
        "paused_at": int(paused_at or 0),
    }


# Providers that support per-(provider, host) auto-pause via the
# round-count threshold model. Single source of truth lives in
# `logic/host_metrics_sampler._PROVIDER_PREFIXES` so adding a seventh
# provider is a one-line change there instead of needing to keep two
# literals in sync. Aliased here as a tuple for the legacy import shape
# (callers iterate / `in` against it). Generic shape — adding a new
# provider in future is the documented six-step contract: (1) extend
# `_PROVIDER_PREFIXES`, (2) add `tuning_<provider>_failure_pause_rounds`
# to TUNABLES, (3) add it to SettingsIn, (4) add to `relocatedTuningKeys`
# in `static/js/app.js`, (5) call `record_provider_outcome` at the probe
# site (or thread `round_threshold=` through the existing
# `_record_failure` site), (6) add an i18n entry under
# `admin.config.fields`.
from logic.host_metrics_sampler import PROVIDER_PREFIXES as _PROVIDER_AUTO_PAUSE_NAMES  # noqa: E402

# Short-TTL cache for the full-table scans behind
# `_provider_pause_state_for_host`. Rebuilt once per cache window;
# 200-host /api/hosts/list pays ONE table scan instead of 200×2.
# 5s TTL is short enough for "live" feel on the chip without burning
# CPU on back-to-back calls within a fan-out burst.
_PROVIDER_STATE_CACHE_TTL = 5.0
_provider_state_cache: dict = {"ts": 0.0, "by_host": {}}


# noinspection PyTypeChecker,PyUnresolvedReferences
def _build_provider_state_index() -> dict:
    """One-shot full-table scan; returns ``{host_id: {provider: stateDict}}``.

    Replaces the per-host leading-wildcard `LIKE '%:host_id'` SELECT
    that turned /api/hosts/list into a 400-scan O(N×rows) problem.
    Single full-table scan now, indexed dict lookup per host.
    """
    by_host: dict = {}
    try:
        with db_conn() as c:
            # Per-(provider, host) failure rows only — the whole-host
            # rows (provider='') are surfaced via _failure_state_for_host.
            fail_rows = c.execute(
                "SELECT host_id, provider, first_failure_ts, consecutive_failures, "
                "paused, last_error, last_failure_ts, paused_at "
                "FROM host_failure_state WHERE provider != ''"
            ).fetchall()
            ok_rows = c.execute(
                "SELECT host_id, provider, last_ok_ts FROM host_provider_last_ok"
            ).fetchall()
    except (sqlite3.Error, OSError):
        return by_host
    for row in fail_rows:
        hid = row[0] or ""
        provider = row[1] or ""
        if not hid or provider not in _PROVIDER_AUTO_PAUSE_NAMES:
            continue
        last_ts = row[6] if row[6] is not None else row[2]
        paused_at = row[7] if row[7] is not None else 0
        by_host.setdefault(hid, {})[provider] = {
            "paused": bool(row[4]),
            "consecutive_failures": int(row[3] or 0),
            "last_error": row[5] or "",
            "first_failure_ts": int(row[2] or 0),
            "last_failure_ts": int(last_ts or 0),
            "paused_at": int(paused_at or 0),
            "last_ok_ts": 0,
        }
    for r in ok_rows:
        hid = r[0] or ""
        provider = r[1] or ""
        if not hid or provider not in _PROVIDER_AUTO_PAUSE_NAMES:
            continue
        ts = int(r[2] or 0)
        existing = by_host.setdefault(hid, {}).get(provider)
        if existing is not None:
            existing["last_ok_ts"] = ts
        else:
            # Healthy provider — no failure-state row but has a last_ok
            # stamp. SPA needs the subtitle even on never-failed hosts.
            by_host.setdefault(hid, {})[provider] = {
                "paused": False,
                "consecutive_failures": 0,
                "last_error": "",
                "first_failure_ts": 0,
                "last_failure_ts": 0,
                "paused_at": 0,
                "last_ok_ts": ts,
            }
    return by_host


def _get_provider_state_index() -> dict:
    """Cached accessor — rebuilds the full-table index when the
    cache is cold or older than `_PROVIDER_STATE_CACHE_TTL`."""
    now = time.time()
    if (now - float(_provider_state_cache.get("ts") or 0.0)) >= _PROVIDER_STATE_CACHE_TTL:
        _provider_state_cache["by_host"] = _build_provider_state_index()
        _provider_state_cache["ts"] = now
    return _provider_state_cache.get("by_host") or {}


def _invalidate_provider_state_cache() -> None:
    """Drop the cached index so the next read does a fresh scan.
    Called from write-paths (`api_hosts_provider_resume`,
    `api_hosts_resume_sampling`, `_sweep_orphan_provider_state_rows`)
    so the operator sees their resume / clear immediately rather than
    waiting up to TTL for the next refresh."""
    _provider_state_cache["ts"] = 0.0
    _provider_state_cache["by_host"] = {}


def _full_host_cache_bust() -> None:
    """Single-flight cache-bust helper — calls BOTH
    ``invalidate_host_provider_cache()`` AND
    ``_invalidate_provider_state_cache()`` so callers can't
    accidentally drift from the contract.

    Any code path that mutates ``host_failure_state`` OR
    ``host_provider_last_ok`` MUST call this (or both invalidators
    individually). Pre-fix the four bulk endpoints called only the
    provider-cache invalidator; the per-provider state-cache row
    stayed cached for up to 5s, leading to chips rendering as
    still-paused right after a Resume. Routing every writer through
    this helper makes the contract impossible to forget.
    """
    invalidate_host_provider_cache()
    _invalidate_provider_state_cache()


def _sqlite_like_escape(s: str) -> str:
    """Escape SQLite LIKE meta-characters (``%``, ``_``, ``\\``) so a
    host id containing them can be embedded in a LIKE pattern without
    matching unrelated rows.

    Pair with ``LIKE ? ESCAPE '\\'`` in the SQL. Example:
    ``f"%:{_sqlite_like_escape(hid)}"`` builds a leading-wildcard
    pattern that matches ONLY ``<provider>:<hid>`` literal forms.

    Without escaping, a curated host id like ``web_01`` would match
    every ``snmp:webX01`` / ``webmin:webY01`` row via the underscore
    wildcard — bulk-resume on the original host would silently
    delete unrelated hosts' failure-state rows. Same trap applied
    to the timeline endpoint's per-host SELECTs.
    """
    return (s or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _provider_pause_state_for_host(host_id: str) -> dict:
    """Per-host slice of the cached full-table provider-state index.

    Pre-fix this did two leading-wildcard `LIKE '%:host_id'` SELECTs
    per call → 200-host /api/hosts/list = 400 full-table scans (~480k
    row comparisons on a 1200-row table). Post-fix the full index is
    built once per ``_PROVIDER_STATE_CACHE_TTL`` window and indexed
    dict-lookup per host. Same shape returned, drop-in for the API
    payload.
    """
    if not host_id:
        return {}
    return dict(_get_provider_state_index().get(host_id) or {})


def _is_provider_paused(host_id: str, provider: str) -> bool:
    """Cheap read-side check used by `_merge_one_host`'s SNMP / Webmin
    blocks to skip the probe entirely when the operator has marked the
    (provider, host) pair as auto-paused. Returns False on any DB error
    — defence-in-depth: a transient SQLite BUSY shouldn't make a paused
    host start probing again. The probe will discover the failure
    naturally and re-pause on the next round."""
    if not host_id or not provider:
        return False
    try:
        with db_conn() as c:
            row = c.execute(
                "SELECT paused FROM host_failure_state "
                "WHERE host_id = ? AND provider = ?",
                (host_id, provider),
            ).fetchone()
    except (sqlite3.Error, OSError):
        return False
    return bool(row and row[0])


# Public alias so `logic/` modules can call this without tripping the
# IDE's access-to-protected-member warning. Mirrors the
# `spawn_background_task` re-export pattern — internal main.py call
# sites keep the underscored name; cross-module callers use the
# public alias.
is_provider_paused = _is_provider_paused


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.get("/api/hosts/list")
async def api_hosts_list(force: bool = False):
    """Skeleton endpoint — curated host list + global state, NO
    per-host probes. Paired with /api/hosts/one/{id} for progressive
    loading: the SPA paints rows immediately from this response, then
    fans out per-host fetches to fill in the stats.

    `force=true` bypasses the 10s `_host_provider_cache` memo. Used
    by the SPA right after a successful host-stats settings save so
    the operator sees the new provider state without waiting up to
    10s for the next natural cache miss.

    Snapshot-first render: each row is pre-populated with the
    last-known `host_*` fields from the persisted `host_snapshots`
    table, with `_stale_fields` / `_stale_ts` markers stamped so the
    SPA renders dimmed values + "X minutes ago" tooltips immediately
    on page load. The per-host fan-out via `/api/hosts/one/{id}`
    upgrades each row from stale to fresh as it lands. Operators
    perceive the page as instant on every repeat visit instead of
    waiting through the cold-cache cliff. First-time visit (empty
    snapshot table) falls through to the legacy skeleton behaviour.

    Cold-load instant paint (Fix A): when the host-provider cache is
    cold OR ``force=true`` is requested, this endpoint NO LONGER
    awaits ``_get_host_provider_state`` synchronously — it serves
    snapshot rows immediately and kicks the hub probe into a
    background asyncio task so subsequent ``/api/hosts/one/{id}``
    fan-out calls (which the SPA fires right after this response
    lands) hit the now-warming cache via the existing single-flight
    lock. Response carries ``hub_probing: true`` when a background
    refresh is in flight so the SPA can render a subtle indicator if
    desired. Warm-cache calls keep the synchronous path — instant
    anyway, and ``state.errors`` / ``state.active`` reflect the latest
    probe.
    """
    from logic.gather import (
        load_host_snapshots as _load_snaps,
        apply_host_snapshot_fallback as _fallback,
    )
    curated = _load_hosts_config()

    # Try the cheap peek first. Warm cache → use it (instant, includes
    # probe errors + freshly-detected active providers). Cold cache OR
    # force=true → serve snapshot rows immediately and kick the probe
    # in the background. The SPA's per-host fan-out via
    # /api/hosts/one/{id} will share the in-flight probe via
    # `_host_provider_lock` so each row's eventual upgrade gets the
    # fresh data without re-paying the hub-probe cost.
    # Always peek for a cached state — if one exists, the response
    # is fresh enough to serve directly. `hub_probing=True` should
    # only fire when there's literally no cached data to serve (cold
    # boot before the first hub probe completes). Pre-fix `force=true`
    # callers (the SPA's `loadHosts(true)` after every bulk action /
    # settings save) skipped the peek and went straight to the
    # kick-probe-and-flag branch, returning `hub_probing=true` on
    # every forced refresh — the topbar spinner span stuck "on"
    # indefinitely because every auto-refresh reset the flag back to
    # true. Now we ALWAYS use the cached state when it exists; force
    # still kicks a background refresh for next-time but doesn't
    # flag the spinner.
    cached_state = _peek_cached_host_provider_state()
    if cached_state is not None:
        state = cached_state
        # Force-bypass with a fresh cached state — kick a background
        # refresh so the next call hits a re-probed cache. Use the
        # strong-reference helper so the task isn't GC'd mid-probe;
        # the print path inside `_get_host_provider_state` would
        # otherwise vanish silently.
        if force:
            try:
                spawn_background_task(
                    _get_host_provider_state(force=force),
                    label="host_provider_state:refresh",
                )
            except RuntimeError:
                pass
        # `hub_probing` reflects whether a hub probe is actually in
        # flight via the single-flight lock. Pre-fix the flag was
        # always set to True on the force-bypass branch, regardless
        # of whether a probe was actually running — every auto-poll
        # would re-set it and the topbar spinner would spin forever.
        # Now the flag tracks live state only.
        hub_probing = _host_provider_lock.locked()
    else:
        # Cheap subset: configured providers from settings (no probe).
        # Empty errors / batch maps — the snapshot fallback below fills
        # the visible fields; per-host fan-out fills live data.
        active_set = active_host_stats_providers()
        state = {
            "active": active_set,
            "beszel_map": {},
            "pulse_map": {},
            "errors": {},
        }
        # Schedule the hub probe so subsequent /api/hosts/one/{id}
        # calls hit a warming cache. Single-flight handles re-entry.
        # Strong-reference helper so a GC sweep can't eat the task
        # before the probe lands its `[hosts] ...` log lines.
        try:
            spawn_background_task(
                _get_host_provider_state(force=force),
                label="host_provider_state:warm",
            )
            hub_probing = True
        except RuntimeError:
            # No event loop (shouldn't happen inside a request handler) —
            # fall back to the legacy synchronous path so the response
            # still carries fresh provider data.
            state = await _get_host_provider_state(force=force)
            hub_probing = False
    any_enabled = bool(state["active"])

    # Load every snapshot once per request — cheap (single SQLite read,
    # ~ms) and amortised across the N curated rows. Build a dict keyed
    # by host id so `apply_host_snapshot_fallback`'s short-hostname
    # tolerance kicks in if the snapshot was keyed by `host01` while
    # the curated id is `host01.example.com` (or vice versa).
    try:
        snapshots: dict = _load_snaps() or {}
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        # Snapshot table not yet migrated, or DB temporarily unavailable
        # — degrade gracefully to "no fallback data".
        snapshots = {}

    hosts = []
    for h in curated:
        if not h.get("enabled", True):
            continue
        # Empty per-host merged dict; the fallback fills `host_*` /
        # `mounts` / `interfaces` from the snapshot when present and
        # stamps `_stale_fields` so the SPA renders dimmed values.
        # When the snapshot for this host doesn't exist (first boot,
        # operator added the row but it hasn't been gathered yet),
        # the fallback is a no-op and the row reverts to the legacy
        # skeleton shape.
        merged: dict = {}
        if snapshots:
            container = {h["id"]: merged}
            try:
                _fallback(container, snapshots)
            except (TypeError, KeyError, AttributeError):
                pass
            merged = container[h["id"]]
        # Port-scan history fold-in — same call as `_merge_one_host`'s
        # path so the LIST endpoint surfaces previously-scanned ports
        # for hosts whose live providers are currently unreachable.
        # Pre-fix the list endpoint shaped each row from snapshot only,
        # never reading `host_port_scans`, so the host-drawer chip
        # strip went empty on stale-provider hosts even though the
        # scan rows were sitting in the DB. The /api/hosts/one/{id}
        # path eventually populated detected_ports via _merge_one_host
        # but the list endpoint is the SPA's primary skeleton paint.
        _populate_detected_ports(h["id"], merged)
        # HTTP probe — populate the merged dict from `host_http_samples`
        # so the skeleton row surfaces the latest probe outcome (status
        # codes, TLS expiry warning, etc.) without waiting for the
        # per-host fan-out. Same shared-helper pattern as detected_ports
        # above: ONE SELECT in the helper, called from both the list
        # endpoint AND `_merge_one_host`.
        try:
            from logic.host_http_sampler import populate_host_http_merge as _http_populate
            _http_populate(h["id"], merged)
        except Exception as e:  # noqa: BLE001
            print(f"[hosts/list] http_probe populate failed for {h.get('id')!r}: {e}")
        # Per-service reachability — stamp `last_probe` on services[]
        # so the skeleton chips render with green / red dots without
        # waiting for the per-host fan-out.
        try:
            from logic.service_sampler import populate_host_service_merge as _svc_populate
            _svc_populate(h["id"], merged)
        except Exception as e:  # noqa: BLE001
            print(f"[hosts/list] service_probe populate failed for {h.get('id')!r}: {e}")
        # Providers list is empty for snapshot-only rows — the SPA's
        # stale-rendering pipeline cues off `_stale_fields` not the
        # providers list. The next `/api/hosts/one/{id}` refresh
        # populates `providers` with the live hits.
        hosts.append(_shape_host_api_row(
            h, merged, [],
            any_provider_enabled=any_enabled,
            active=state["active"],
        ))
    agg_error = "; ".join(f"{k}: {v}" for k, v in state["errors"].items()) or None
    return {
        "configured": bool(state["active"]),
        "active": sorted(state["active"]),
        "error": agg_error,
        "provider_errors": state["errors"],
        "hub_url": get_setting(Settings.BESZEL_HUB_URL) or "",
        "hosts": hosts,
        "curated_count": len(curated),
        "enabled_count": sum(1 for h in curated if h.get("enabled", True)),
        # True when the response was served before the hub probe
        # finished — SPA may render a subtle "refreshing…" hint. The
        # per-host fan-out via /api/hosts/one/{id} naturally upgrades
        # each row from stale to fresh as the probe completes, so no
        # additional polling is required from the SPA on this flag.
        "hub_probing": hub_probing,
    }


async def _hosts_one_inner(h: dict, *, force: bool, client_id: str | None = None):
    """Inner helper for `/api/hosts/one/{host_id}` — fetches the
    provider state then merges this host's row. Split out so the
    outer endpoint can wrap the whole sequence in `asyncio.wait_for`.
    """
    state = await _get_host_provider_state(force=force)
    merged_pair = await _merge_one_host(h, state, force=force, client_id=client_id)
    return state, merged_pair


@app.get("/api/hosts/one/{host_id}")
async def api_hosts_one(host_id: str, request: Request, force: bool = False):
    """Merge ONE curated host with provider data.

    Called N times in parallel by the SPA after /api/hosts/list
    returns the skeleton. The shared Beszel/Pulse cache ensures the
    batch probes run at most once per TTL window.

    ``force=true`` mirrors the parallel param on ``/api/hosts/list``
     and bypasses the 10s provider-state cache so a host drawer
    re-opened immediately after Admin → Hosts Save sees fresh provider
    data instead of waiting out the TTL
    """
    curated = _load_hosts_config()
    h = next((x for x in curated if x.get("id") == host_id), None)
    if h is None or not h.get("enabled", True):
        raise HTTPException(404, f"Host not found: {host_id}")
    # Outer per-host budget (30s). With single-flight
    # `_get_host_provider_state` lock, the cold-cache Beszel+Pulse cost
    # is paid by the FIRST caller only; subsequent fan-out calls reuse
    # the populated cache. Worst-case for the first caller is
    # ~15s Beszel + ~15s Pulse + ~10s NE + ~20s Webmin sequentially,
    # but NE has its own 10s `httpx` timeout and Webmin its own 20s
    # `asyncio.wait_for`, so a single laggy provider can't blow past
    # this budget. 30s comfortably under any reasonable NPM
    # `proxy_read_timeout` (default 60s) so OmniGrid's explicit 504
    # always fires first, never NPM's generic gateway timeout.
    # capture probe wall-clock so the SPA can hover-title a
    # "took Xs" hint on the row. Useful when a host shows `unknown`
    # status: operators can see at a glance whether it was a fast 5xx
    # or a slow 30s hang, without grepping logs.
    _probe_start = time.monotonic()
    try:
        state, (merged, providers) = await asyncio.wait_for(
            _hosts_one_inner(h, force=force, client_id=_request_client_id(request)),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"per-host probe budget exceeded (30s) for {host_id}",
        )
    probe_elapsed_ms = int((time.monotonic() - _probe_start) * 1000)
    any_enabled = bool(state["active"])
    row = _shape_host_api_row(
        h, merged, providers,
        any_provider_enabled=any_enabled,
        active=state["active"],
    )
    row["_probe_elapsed_ms"] = probe_elapsed_ms
    # NO SSE publish here. Earlier this endpoint published
    # `host:row_updated` so other tabs would see the freshly-merged row
    # — but the SAME tab subscribes to the bus, so the event triggered
    # the SPA's `host:row_updated` listener, which called
    # `refreshHostRow(id)`, which hit THIS endpoint again, which
    # published another event. Self-sustaining infinite loop, amplified
    # by N hosts × M tabs. Reads aren't a state change; the events that
    # legitimately need to push host updates (`host:failure_state_changed`,
    # `host:history_appended`) are still published from
    # `host_metrics_sampler`. Other tabs catch up on the next poll cycle
    # (the SPA gracefully degrades to 30s polling when SSE is connected
    # — see CLAUDE.md's polling-fallback bullet).
    return {"host": row}


def _load_hosts_config() -> list[dict]:
    """Parse the ``hosts_config`` JSON setting into a validated list.

    Empty / invalid values return an empty list. Caller treats an empty
    list as "no curated hosts — fall back to auto-discovery where
    applicable."
    """
    # Routed through `load_settings_json` so the parse / isinstance /
    # fallback dance lives in one place.
    parsed = load_settings_json(
        Settings.HOSTS_CONFIG, default=[], expected_type=list,
    )
    if not parsed:
        return []
    clean: list[dict] = []
    for i, h in enumerate(parsed):
        if not isinstance(h, dict):
            continue
        hid = (h.get("id") or h.get("name") or "").strip()
        if not hid:
            continue
        clean.append({
            "id": hid,
            # Empty label is INTENTIONAL post-frontend's
            # `hostDisplayName(h)` resolver falls back to the asset
            # inventory's name when this is blank. The previous
            # `or hid` fallback (kept for years pre-asset-inventory)
            # silently overwrote that intent on EVERY load, defeating
            # the save-side fixes /. Pass the literal
            # stored value through.
            "label": (h.get("label") or "").strip(),
            "ne_url": (h.get("ne_url") or "").strip(),
            "beszel_name": (h.get("beszel_name") or "").strip(),
            "pulse_name": (h.get("pulse_name") or "").strip(),
            # Webmin per-host name — currently unused for lookup (every
            # Webmin install has its own Miniserv URL), but retained so
            # the admin editor has a slot to tag which row a discovered
            # Webmin host maps to. The actual probe URL lives in the
            # webmin_aliases map.
            "webmin_name": (h.get("webmin_name") or "").strip(),
            # Optional external URL the operator picks (e.g. the host's
            # web UI). Rendered as a clickable link in the Hosts view's
            # SYSTEM card, matches Beszel's "+ Add URL" affordance.
            "url": (h.get("url") or "").strip(),
            # Optional icon override — a slug like "opnsense" (resolved
            # to /img/icons/opnsense.svg) or a full URL. Empty = let
            # the frontend's iconUrlFor() auto-resolve from the host's
            # id / label.
            "icon": (h.get("icon") or "").strip(),
            # Operator-assigned catalogue number. Used today for sort
            # ordering + grouping in the Hosts view; future scope is
            # the primary key for PersonalSite inventory lookups so
            # hardware / location / NIC metadata can be pulled back in.
            # Empty / invalid values → None so "no number" sorts last.
            "custom_number": _coerce_int(h.get("custom_number")),
            # Free-text IP field — operator-maintained, not auto-derived
            # from `ne_url` / DNS / asset inventory. Stored as-typed so
            # the operator can put "192.X.X.X", "192.X.X.X/24", or
            # "fe80::1" and we don't second-guess. No filter impact
            # today; captured so the Hosts drawer can display it and
            # a future group-filter iteration can parse it.
            "ip": (h.get("ip") or "").strip()[:64],
            # Dedicated probe target — hostname OR IP that every probe
            # path falls back to when its provider-specific override is
            # empty (port-scan / ping / SNMP / SSH). Independent of any
            # provider so disabling SNMP / ping / SSH never leaves the
            # other probes without a target. Distinct from the legacy
            # `ip` field above (which is display-only metadata, often
            # carrying CIDR / subnet notation that's not a connect()
            # target). Free-text, max 64 chars; accepts an IP literal
            # (`192.X.X.X`) OR a hostname (`firewall.example.com`) the
            # OmniGrid container's resolver can reach.
            "address": (h.get("address") or "").strip()[:64],
            # Per-host SSH override sub-dict. Optional user / port /
            # disabled / host override — the key material itself lives
            # in the GLOBAL ssh_default_private_key setting (V1 scope:
            # single global key). Missing or non-dict values collapse
            # to {} so downstream code can always do dict.get(...).
            "ssh": _clean_host_ssh(h.get("ssh")),
            # Per-host ping opt-in. Default OFF — operator opts
            # in per host. Optional `port` + `transport` overrides
            # cascade over the globals.
            "ping": _clean_host_ping(h.get("ping")),
            # SNMP target alias — Docker hostname → SNMP-reachable
            # name/IP when the curated row's id isn't directly addressable
            # by the SNMP agent. Empty falls through to the global
            # snmp_aliases map and finally to the bare id.
            "snmp_name": (h.get("snmp_name") or "").strip(),
            # Per-host SNMP override sub-dict. Optional community /
            # version / port / v3_user / v3_auth_key / v3_priv_key —
            # any unset key falls through to the global default. {} =
            # "no override" (the common case).
            "snmp": _clean_host_snmp(h.get("snmp")),
            # Per-host HTTP probe opt-in. Default OFF — operator opts
            # in per host (mirrors ping.enabled / snmp.enabled). Carries
            # an optional URL override list, content-match string,
            # accepted-status-codes set, and verify_tls flag. {} = "no
            # per-host config" (and the master toggle is OFF or no URLs
            # to probe).
            "http_probe": _clean_host_http_probe(h.get("http_probe")),
            # Per-host port-scan override sub-dict. Optional `enabled`
            # / `ports` / `timeout_s` / `concurrency` — any unset key
            # falls through to the global default. {} = "use globals
            # AND inherit the global enabled flag".
            "port_scan": _clean_host_port_scan(h.get("port_scan")),
            # Per-row services[] list — operator's curated chip strip
            # of service links + optional probe sub-dicts. Each entry
            # carries {name?, url?, icon?, probe?: {enabled, type,
            # port, path, expected_status}}. Validated via
            # `_clean_host_services` to drop bad shapes; empty list
            # when no services configured.
            "services": _clean_host_services(h.get("services")),
            "enabled": bool(h.get("enabled", True)),
        })
    return clean


# noinspection PyTypeChecker,PyUnresolvedReferences
def _clean_host_port_scan(raw: Any) -> dict:
    """Normalise the per-host ``port_scan`` sub-dict.

    Accepts ``enabled`` (bool, defaults to inheriting the global
    master toggle), ``ports`` (CSV/range string — validated via
    `parse_port_csv`; empty → use global default), ``timeout_s``
    (1..30 int), and ``concurrency`` (1..256 int). Unknown keys
    drop. Empty → empty dict, which ``effective_port_scan_config``
    reads as "use globals across the board".
    """
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    if "enabled" in raw:
        out["enabled"] = bool(raw.get("enabled"))
    ports_raw = raw.get("ports")
    if isinstance(ports_raw, str) and ports_raw.strip():
        # Validate via parse_port_csv; reject empty parse results so
        # an obviously-broken CSV ("zzz,abc") doesn't silently
        # disable the override.
        try:
            from logic.port_scanner import parse_port_csv as _pcsv
            if _pcsv(ports_raw):
                out["ports"] = ports_raw.strip()
        except (ImportError, ValueError, TypeError):
            pass
    try:
        t = raw.get("timeout_s")
        if t is not None:
            ti = int(t)
            if 1 <= ti <= 30:
                out["timeout_s"] = ti
    except (TypeError, ValueError):
        pass
    try:
        c = raw.get("concurrency")
        if c is not None:
            ci = int(c)
            if 1 <= ci <= 256:
                out["concurrency"] = ci
    except (TypeError, ValueError):
        pass
    return out


# noinspection PyTypeChecker,PyUnresolvedReferences
def _clean_host_ssh(raw: Any) -> dict:
    """Normalise the per-host ``ssh`` sub-dict.

    Accepts only the keys that make sense at V1 (``user`` / ``port``
    / ``host`` / ``fqdn`` / ``password`` / ``enabled``) and coerces
    their types. Unknown keys are dropped so a malformed import can't
    smuggle arbitrary fields into the persisted JSON. Empty → empty
    dict, which the SSH module treats as "host opted OUT of SSH"
    under the post-fix opt-in semantics.

    Pre-fix the gate field was ``disabled`` (off-when-set, default =
    inherit global). Post-fix it's ``enabled`` (on-when-set, default
    = host is OFF). Inputs with the legacy ``disabled`` key are
    silently dropped here — the client-side ``norm()`` in
    `static/js/app.js` already converts old-shape backups at import
    time, and the schema migration in ``logic/migrations.py`` rewrites
    every existing DB row, so by the time data reaches this validator
    it should always be the new shape. Defensive: if a stray legacy
    POST arrives with ``disabled: false``, we treat it as the
    pre-flip "implicitly enabled" intent and write ``enabled: true``.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    user = str(raw.get("user") or "").strip()
    if user:
        out["user"] = user
    host = str(raw.get("host") or "").strip()
    if host:
        out["host"] = host
    # `fqdn` is an alias for `host` (operator-facing naming). The
    # resolve function reads both, preferring whichever is set;
    # persist as-typed so the editor round-trips the operator's
    # choice.
    fqdn = str(raw.get("fqdn") or "").strip()
    if fqdn:
        out["fqdn"] = fqdn
    port = raw.get("port")
    if port not in (None, "", 0):
        try:
            p = int(port)
            if 1 <= p <= 65535:
                out["port"] = p
        except (TypeError, ValueError):
            pass
    # Per-host password override. Stored in the hosts_config JSON
    # (which already contains other secrets implicitly, e.g. webmin
    # URLs with credentials). The admin-only /api/hosts/config
    # endpoint gates access; /api/hosts/debug masks the ssh sub-dict
    # so per-host passwords don't leak to the debug panel.
    password = str(raw.get("password") or "")
    if password:
        out["password"] = password
    # New `enabled` flag. ONLY explicit `enabled: true` writes
    # the flag through; everything else (absent, false, legacy
    # `disabled` field) leaves the row in the new "OFF until opted in"
    # default. The schema migration in `logic/migrations.py` (the
    # numbered SSH opt-in flip) handles legacy data on first boot —
    # DO NOT add a defensive
    # `disabled` fallback here: the writer runs on
    # every save, not just at import, and any "fall back to enabled
    # when not explicitly disabled" branch would re-enable rows the
    # operator just unchecked elsewhere in the same save.
    # `is True` is intentional — see docstring above
    # noinspection PySimplifyBooleanCheck,PyComparisonWithCallableTrueFalse
    if raw.get("enabled") is True:
        out["enabled"] = True
    return out


# noinspection PyTypeChecker,PyUnresolvedReferences
def _clean_host_ping(raw: Any) -> dict:
    """Normalise the per-host ``ping`` sub-dict.

    Accepts ``enabled`` (bool, default False), ``port`` (int 1..65535
    or null = use global ``ping_default_port``), and ``transport``
    (``"tcp"`` / ``"icmp"`` / null = use global ``ping_use_icmp``).
    Unknown keys are dropped; malformed types collapse to default.
    Empty input → empty dict (= "no per-host config" — which itself
    means "ping NOT enabled for this host" because the gate defaults
    to OFF).
    """
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    if bool(raw.get("enabled")):
        out["enabled"] = True
    port = raw.get("port")
    if port not in (None, "", 0):
        try:
            p = int(port)
            if 1 <= p <= 65535:
                out["port"] = p
        except (TypeError, ValueError):
            pass
    t = (str(raw.get("transport") or "")).strip().lower()
    if t in ("tcp", "icmp"):
        out["transport"] = t
    return out


# noinspection PyTypeChecker,PyUnresolvedReferences
def _clean_host_snmp(raw: Any) -> dict:
    """Normalise the per-host ``snmp`` override sub-dict.

    Accepts every per-host SNMP override on a curated row:
      * ``community`` (str)        — overrides ``snmp_default_community``
      * ``version``   ("v2c"/"v3") — overrides ``snmp_default_version``
      * ``port``      (1..65535)   — overrides ``snmp_default_port``
      * ``v3_user``   (str)        — overrides ``snmp_v3_user``
      * ``v3_auth_key`` (str)      — overrides ``snmp_v3_auth_key``
      * ``v3_priv_key`` (str)      — overrides ``snmp_v3_priv_key``
      * ``walk_concurrency`` (1..16) — overrides
        ``tuning_snmp_per_host_walk_concurrency``. Range matches the
        global tunable's bounds; pre-fix this clamped to 1..32 which
        was internally inconsistent with the global 1..16. Server-class BMCs
        (Dell iDRAC, Cisco IMC, Supermicro IPMI) handle parallel
        queries fine and benefit dramatically from > 1 because pysnmp
        v7's per-walk overhead serialises ~67 OID branches into 30-50s
        wall-clock at concurrency=1. Low-power embedded snmpd's stay
        at the safety-floor default 1 by omitting this field.

    Empty / missing input → empty dict (the common case — most rows
    inherit every default). Unknown keys are dropped silently so a
    malformed import or a stale field name can't smuggle arbitrary
    data through. v3 keys persist VERBATIM in the curated JSON; admin
    is the only role that reads/writes ``hosts_config``, and the
    backup tooling already redacts the file via the same path it
    redacts ssh.password — see logic.backups.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    # explicit per-host enable flag, parallel to ping.enabled.
    # Opt-IN semantics: persist `enabled: True` only when the operator
    # explicitly checked the box; drop the field otherwise so the row
    # JSON stays tight. `_merge_one_host` reads `enabled is True` (no
    # default-true fallback), so a fresh row with snmp_name set but no
    # explicit opt-in does NOT probe — the operator must check the
    # per-host SNMP enable box. Mirrors `_clean_host_ping`'s pattern.
    # omission == disabled is INTENTIONAL. The read-side gate
    # (`enabled is True`) interprets a missing `enabled` key as OFF
    # rather than ON, so the SPA's strip-blanks pattern that drops
    # `enabled: false` to keep JSON tight stays correct. DON'T re-add
    # a default-true fallback here.
    if bool(raw.get("enabled")):
        out["enabled"] = True
    community = (str(raw.get("community") or "")).strip()
    if community:
        out["community"] = community
    version = (str(raw.get("version") or "")).strip().lower()
    if version in ("v2c", "v3"):
        out["version"] = version
    port = raw.get("port")
    if port not in (None, "", 0):
        try:
            p = int(port)
            if 1 <= p <= 65535:
                out["port"] = p
        except (TypeError, ValueError):
            pass
    for k in ("v3_user", "v3_auth_key", "v3_priv_key"):
        v = (str(raw.get(k) or "")).strip()
        if v:
            out[k] = v
    walk_conc = raw.get("walk_concurrency")
    if walk_conc not in (None, "", 0):
        try:
            wc = int(walk_conc)
            if 1 <= wc <= 16:
                out["walk_concurrency"] = wc
        except (TypeError, ValueError):
            pass
    # Per-host wall-clock budget override. Same shape as
    # ``walk_concurrency`` — overrides
    # ``tuning_snmp_wall_clock_budget_seconds`` when supplied; falls
    # through to the tunable when blank. Lets a slow iDRAC pin a 90s
    # budget while the rest of the fleet stays at the 60s default.
    # Range 5..600 (matches the global tunable's bounds).
    wcb = raw.get("wall_clock_budget")
    if wcb not in (None, "", 0):
        try:
            wcb_i = int(wcb)
            if 5 <= wcb_i <= 600:
                out["wall_clock_budget"] = wcb_i
        except (TypeError, ValueError):
            pass
    # Per-host vendor MIB selector. Operator-declared list of vendor
    # MIBs to walk against THIS host (subset of dell / cisco / apc / ucd
    # / synology / printer). Empty / missing = auto-detect from sysDescr
    # (current default behaviour). Trims the OID set to base + matching
    # vendors so an iDRAC doesn't waste budget walking Cisco / APC /
    # Synology / Printer MIBs that always return noSuchObject.
    # Vendor key set is sourced from ``logic.snmp._VALID_VENDOR_KEYS``
    # (single source of truth — also exposed to the SPA via
    # ``client_config.snmp_vendor_keys`` on /api/me).
    cleaned = _clean_vendors_input(raw.get("vendors"))
    if cleaned:
        out["vendors"] = sorted(cleaned)
    # Per-host mount-exclusion list. Operator-supplied mount paths
    # to drop from the SNMP storage extractor's output (in addition
    # to the universal pseudo-fs prefixes in
    # ``logic.snmp._DEFAULT_EXCLUDE_MOUNT_PREFIXES``). Each entry
    # is matched as either an EXACT path or a prefix-with-slash
    # (e.g. ``"/opt"`` matches both bare ``/opt`` AND ``/opt/foo``).
    # Validates as a list of non-empty strings; non-strings dropped
    # silently; max 32 entries to keep the per-row blob bounded.
    raw_excl = raw.get("exclude_mounts")
    if isinstance(raw_excl, list):
        cleaned_excl = []
        for item in raw_excl[:32]:
            if isinstance(item, str):
                s = item.strip()
                if s:
                    cleaned_excl.append(s)
        if cleaned_excl:
            out["exclude_mounts"] = cleaned_excl
    return out


def _clean_host_http_probe(raw: Any) -> dict:
    """Normalise the per-host ``http_probe`` sub-dict.

    Accepts ``enabled`` (bool, default False), ``urls`` (list-of-str or
    textarea content), ``content_match`` (str ≤ 256 chars, DoS guard),
    ``accepted_status_codes`` (CSV / list), and ``verify_tls`` (bool,
    default True). Unknown keys are dropped; malformed types collapse
    to defaults. Empty input → empty dict (= "no per-host config" /
    inherit-from-curated-url chain).

    URLs MUST start with ``http://`` or ``https://``; non-http(s)
    schemes are dropped silently. Each URL is whitespace-trimmed
    and the list deduped via ``parse_urls_textarea``.

    Status codes accept either a CSV string (``"200,301,302"``) OR a
    list / int. Codes outside 100..599 are dropped. Empty list →
    backend falls back to the 2xx default range.

    Mirrors ``_clean_host_ping`` / ``_clean_host_snmp``'s opt-in
    pattern: persist ``enabled: True`` only when the operator
    explicitly ticked the box; omit the key otherwise so the row
    JSON stays tight.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    if bool(raw.get("enabled")):
        out["enabled"] = True
    # URLs — accept list OR newline / comma-separated string. Reject
    # anything that doesn't parse as http(s).
    raw_urls = raw.get("urls")
    if raw_urls is not None:
        from logic.http_probe import parse_urls_textarea as _parse_urls
        parsed = _parse_urls(raw_urls)
        kept: list[str] = []
        for u in parsed:
            ul = u.strip().lower()
            if ul.startswith("http://") or ul.startswith("https://"):
                kept.append(u.strip())
        if kept:
            out["urls"] = kept
    content_match = raw.get("content_match")
    if isinstance(content_match, str):
        s = content_match.strip()
        if s and len(s) <= 256:
            out["content_match"] = s
    raw_codes = raw.get("accepted_status_codes")
    if raw_codes is not None and raw_codes != "":
        from logic.http_probe import parse_status_codes_csv as _parse_codes
        codes = _parse_codes(raw_codes)
        if codes:
            out["accepted_status_codes"] = codes
    if "verify_tls" in raw:
        # Explicit boolean coercion so a literal ``false`` is preserved;
        # missing key (or other falsy form) collapses to the default
        # (True) at the consumer site.
        out["verify_tls"] = bool(raw.get("verify_tls"))
    return out


def _clean_host_services(raw: Any) -> list[dict]:
    """Normalise the per-host ``services[]`` list.

    Each entry is one curated service chip the operator pinned to a
    host. Schema:

        {
            "name": str,       # display label, max 64 chars
            "url": str,        # optional clickable link, max 256 chars
            "icon": str,       # optional icon slug, max 64 chars
            "catalog_id": int, # optional FK to service_catalog template
            "probe": {         # optional per-chip reachability probe
                "enabled": bool,
                "type": "tcp" | "http",       # default "tcp"
                "port": int | None,            # 1..65535 (single-port legacy)
                "path": str,                   # http-only, "/" default
                "expected_status": int,        # 100..599, 0 = 2xx default
                "ports": [                     # multi-port (Apps feature)
                    {
                        "port": int,           # 1..65535
                        "protocol": "tcp" | "udp" | "http" | "https",
                        "label": str,          # max 64
                        "probe_path": str,     # http-only, max 256
                        "probe_status": int,   # 0..599, 0 = 2xx default
                    },
                    ...
                ]
            }
        }

    Unknown keys drop; bad types collapse to defaults. Returns ``[]``
    when input isn't a list or is empty.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        cleaned: dict = {}
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            cleaned["name"] = name.strip()[:64]
        url = entry.get("url")
        if isinstance(url, str) and url.strip():
            cleaned["url"] = url.strip()[:256]
        icon = entry.get("icon")
        if isinstance(icon, str) and icon.strip():
            cleaned["icon"] = icon.strip()[:64]
        # Optional catalog_id (FK to service_catalog template). Operator
        # can link a chip to a reusable recipe so name / icon / ports
        # inherit from the template — the chip's per-host overrides take
        # precedence at render time.
        ci = _coerce_int_local(entry.get("catalog_id"))
        if ci is not None and 1 <= ci <= 2 ** 31 - 1:
            cleaned["catalog_id"] = ci
        # Preserve every other operator-set field we don't actively
        # validate (status, locked_fields[], asset_id, etc.) so service
        # discovery + manual edits round-trip cleanly through save.
        # Whitelist these explicit pass-through keys to avoid trash
        # being persisted.
        for k in ("status", "locked_fields", "asset_id", "label"):
            if k in entry:
                cleaned[k] = entry[k]
        # Per-chip probe sub-dict.
        probe = entry.get("probe")
        if isinstance(probe, dict):
            probe_out: dict = {}
            if bool(probe.get("enabled")):
                probe_out["enabled"] = True
            probe_type = probe.get("type")
            if isinstance(probe_type, str) and probe_type.strip().lower() in ("tcp", "http"):
                probe_out["type"] = probe_type.strip().lower()
            port = probe.get("port")
            if isinstance(port, (int, str)) and port != "":
                try:
                    pi = int(port)
                    if 1 <= pi <= 65535:
                        probe_out["port"] = pi
                except (TypeError, ValueError):
                    pass
            path = probe.get("path")
            if isinstance(path, str) and path.strip():
                probe_out["path"] = path.strip()[:256]
            exp = probe.get("expected_status")
            if isinstance(exp, (int, str)) and exp != "":
                try:
                    ei = int(exp)
                    if 100 <= ei <= 599:
                        probe_out["expected_status"] = ei
                except (TypeError, ValueError):
                    pass
            # Multi-port probe shape (Apps feature). Each port has its
            # own probe verb so a chip with N ports (Portainer 9000 +
            # 9443) tracks each independently. Routed through
            # service_catalog._coerce_ports to share validation with
            # the catalog's default_ports list.
            ports_raw = probe.get("ports")
            if isinstance(ports_raw, list) and ports_raw:
                from logic.service_catalog import coerce_ports
                ports_clean = coerce_ports(ports_raw)
                if ports_clean:
                    probe_out["ports"] = ports_clean
            if probe_out:
                cleaned["probe"] = probe_out
        if cleaned:
            out.append(cleaned)
    return out


def _clean_vendors_input(raw: Any) -> Optional[set[str]]:
    """Normalise an SNMP ``vendors`` list to the canonical lowercase set.

    Accepts the raw value from JSON (list-of-strings expected). Returns
    ``None`` when the input isn't a non-empty list. Otherwise returns a
    set containing only the entries that (a) are strings, (b) match the
    canonical vendor key set sourced from
    ``logic.snmp._VALID_VENDOR_KEYS``. This is the single boundary at
    which non-string entries are filtered out so callers that consume
    the result downstream don't need defensive ``isinstance`` checks.
    """
    if not isinstance(raw, list) or not raw:
        return None
    from logic.snmp import VALID_VENDOR_KEYS as _VALID_VENDOR_KEYS
    cleaned = {
        str(v).strip().lower() for v in raw
        if isinstance(v, str) and str(v).strip().lower() in _VALID_VENDOR_KEYS
    }
    return cleaned or None


def _snmp_vendor_keys_sorted() -> list[str]:
    """Return the SNMP vendor key set as a sorted list for /api/me.

    Sourced from ``logic.snmp._VALID_VENDOR_KEYS`` so a vendor added to
    `_VENDOR_SIGNATURES` automatically surfaces a checkbox in the SPA's
    Admin → Hosts editor on the next /api/me round-trip without
    touching the frontend.
    """
    from logic.snmp import VALID_VENDOR_KEYS as _VALID_VENDOR_KEYS
    return sorted(_VALID_VENDOR_KEYS)


def _slugify_action(title: str) -> str:
    """Derive a stable slug from a user-typed action title.

    Used as the `id` for SSH custom actions when the operator didn't
    supply one explicitly. Kept permissive — lowercase letters /
    numbers / hyphens only, truncated to 40 chars. Collisions aren't
    checked (two actions titled identically will produce the same
    slug — same behaviour as schedule names, operator's problem).
    """
    import re as _re
    s = (title or "").strip().lower()
    s = _re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:40] or "action"


def _coerce_int(v) -> Optional[int]:
    """Accept an int, a numeric string, or empty/garbage — return int or None."""
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _save_hosts_config(hosts: list[dict]) -> list[dict]:
    """Persist the curated hosts list and return what we saved.

    Rejects bad shapes at the boundary so downstream code can trust the
    result. Duplicates by ``id`` collapse to the last-wins record.
    """
    if not isinstance(hosts, list):
        raise HTTPException(400, "hosts must be a list")

    # Duplicate-custom_number check — must run BEFORE id-dedup,
    # because the UI may send two entries with the same cn but
    # different ids and we want to point the operator at both of
    # them (the id-dedup loop below would collapse same-id rows
    # but leave different-id / same-cn rows in, which is what
    # this check catches).
    by_cn: dict[int, list[str]] = {}
    for h in hosts:
        if not isinstance(h, dict):
            continue
        cn = _coerce_int(h.get("custom_number"))
        if cn is None:
            continue
        hid = (h.get("id") or h.get("name") or "").strip() or "(unnamed)"
        by_cn.setdefault(cn, []).append(hid)
    dupes = {cn: ids for cn, ids in by_cn.items() if len(ids) > 1}
    if dupes:
        parts = [f"#{cn} ({', '.join(ids)})" for cn, ids in sorted(dupes.items())]
        plural = "numbers are" if len(parts) > 1 else "number is"
        raise HTTPException(
            400,
            "These custom " + plural + " used by more than one host: "
            + "; ".join(parts) + ". "
                                 "Each host needs its own unique custom number — change the "
                                 "conflicting ones and try Save again.",
        )

    # Duplicate-id check — without this, two rows with the same id
    # would silently collapse via `seen[hid] = ...` (last wins),
    # losing the first row + its custom_number / IP / SSH overrides
    # without any error to the operator (in the code review).
    id_counts: dict[str, int] = {}
    for h in hosts:
        if not isinstance(h, dict):
            continue
        hid = (h.get("id") or h.get("name") or "").strip()
        if not hid:
            continue
        id_counts[hid] = id_counts.get(hid, 0) + 1
    id_dupes = sorted(hid for hid, n in id_counts.items() if n > 1)
    if id_dupes:
        names = ", ".join(id_dupes)
        plural = "hosts share these ids" if len(id_dupes) > 1 else "host id"
        raise HTTPException(
            400,
            "Two or more " + plural + ": " + names + ". "
                                                     "Each host needs its own unique id — rename the duplicates "
                                                     "and try Save again.",
        )

    seen: dict[str, dict] = {}
    for h in hosts:
        if not isinstance(h, dict):
            raise HTTPException(400, "every host entry must be an object")
        hid = (h.get("id") or h.get("name") or "").strip()
        if not hid:
            raise HTTPException(400, "host entry is missing 'id'")
        seen[hid] = {
            "id": hid,
            # Empty label is INTENTIONAL post-the SPA's
            # `hostDisplayName(h)` resolver falls back to the asset
            # inventory's name when this is blank. DO NOT auto-fill
            # with `hid` here: that would silently overwrite an empty
            # operator intent with the host id, defeating the
            # "inherit from asset" feature on every save.
            "label": (h.get("label") or "").strip(),
            "ne_url": (h.get("ne_url") or "").strip(),
            "beszel_name": (h.get("beszel_name") or "").strip(),
            "pulse_name": (h.get("pulse_name") or "").strip(),
            "webmin_name": (h.get("webmin_name") or "").strip(),
            "url": (h.get("url") or "").strip(),
            "icon": (h.get("icon") or "").strip(),
            # Operator-assigned catalogue number. Persisted so the
            # Hosts-view "Custom #" sort + future asset-inventory
            # lookups find the right row. Blank / non-numeric → None
            # via _coerce_int (same path _load_hosts_config uses).
            "custom_number": _coerce_int(h.get("custom_number")),
            # Free-text IP — see _load_hosts_config for rationale.
            "ip": (h.get("ip") or "").strip()[:64],
            # Dedicated probe target — hostname OR IP. Used as fallback
            # by port-scan / ping / SNMP / SSH when no provider-specific
            # override is set. See _load_hosts_config for full rationale.
            "address": (h.get("address") or "").strip()[:64],
            # Per-host SSH override block — see _clean_host_ssh for
            # the shape contract. {} when no override is set.
            "ssh": _clean_host_ssh(h.get("ssh")),
            # Per-host ping opt-in.
            "ping": _clean_host_ping(h.get("ping")),
            # Per-host SNMP target alias + per-row override block.
            "snmp_name": (h.get("snmp_name") or "").strip(),
            "snmp": _clean_host_snmp(h.get("snmp")),
            # Per-host HTTP probe override block — see
            # `_clean_host_http_probe` for the shape contract. {} when
            # no override is set (the common case — operator opts in
            # by checking the per-host enable box AND relying on the
            # curated `url` / `services[].url` chain).
            "http_probe": _clean_host_http_probe(h.get("http_probe")),
            # Per-row services[] — preserved on save so the operator's
            # curated chip strip survives a hosts_config write. See
            # `_clean_host_services` for the validator + probe shape.
            "services": _clean_host_services(h.get("services")),
            "enabled": bool(h.get("enabled", True)),
        }
        # host-level enable gates every per-provider enable.
        # Defence-in-depth on top of the SPA's strip in saveHostsConfig:
        # if the row is disabled, force every provider's `enabled` flag
        # to drop so a malformed POST (or a future caller bypassing the
        # SPA) can't persist `enabled: true` on any provider sub-dict.
        if not seen[hid]["enabled"]:
            for _provider_key in ("ssh", "ping", "snmp", "http_probe"):
                _sub = seen[hid].get(_provider_key)
                if isinstance(_sub, dict):
                    _sub.pop("enabled", None)
    ordered = list(seen.values())
    set_setting(Settings.HOSTS_CONFIG, json.dumps(ordered))
    return ordered


@app.get("/api/hosts/config")
async def api_hosts_config_get(_u: AdminUser):
    """Admin-only: return the curated host list used by the Hosts tab."""
    return {"hosts": _load_hosts_config()}


@app.post("/api/hosts/config")
async def api_hosts_config_set(
    body: dict,
    _u: AdminUser,
):
    """Admin-only: replace the curated host list.

    Full-replace rather than per-row CRUD — the list is small (one row
    per physical host) and the UI saves the whole table on each edit.
    Keeps the backend state machine trivial.
    """
    hosts = body.get("hosts")
    saved = _save_hosts_config(hosts if isinstance(hosts, list) else [])
    _cache["ts"] = 0  # force next gather to pick up new mappings
    # Host-config rows feed provider name resolution (beszel_name /
    # pulse_name / webmin_name aliases). Drop the provider state cache
    # so /api/hosts/one/{id} doesn't serve up to 10s of stale results
    # using the old aliases. Same rationale as in api_set_settings.
    invalidate_host_provider_cache()
    # Sweep orphan rows in `host_failure_state` + `host_provider_last_ok`
    # for hosts that are no longer in the curated list. Without this,
    # rows accumulate forever after a host is deleted, eventually
    # degrading the LIKE-scan performance of `_provider_pause_state_for_host`.
    # Best-effort — a sweep failure must not roll back the operator's
    # save; just log and move on.
    try:
        _sweep_orphan_provider_state_rows({h.get("id") for h in saved if h.get("id")})
    except Exception as e:
        print(f"[hosts] orphan sweep failed: {e}")
    _invalidate_provider_state_cache()  # next /api/hosts/list rebuilds from clean state
    # Drop the host_metrics_sampler's host_provider_config cache so the
    # defensive guard in `record_provider_outcome` picks up the new
    # mapping on the next probe. Without this, a probe that fires within
    # the cache TTL after a save would still see the OLD mapping and
    # could record a failure for a provider the operator just removed.
    try:
        from logic.host_metrics_sampler import invalidate_host_provider_config_cache
        invalidate_host_provider_config_cache()
    except Exception as e:
        print(f"[hosts] host_provider_config cache invalidate failed: {e}")
    # Audit row — full-replace of the curated host list is the single
    # largest operator-visible mutation in the app (provider mappings,
    # SNMP credentials, ping toggles). The message carries the row
    # count so the History pane gives a hint about the size of the change
    # without dumping the JSON. Diff-stats (added / removed / modified)
    # are deliberately deferred — they'd need a snapshot of the
    # pre-save list, which doubles the query cost on every Save; the
    # row-count signal is enough for "did the operator save anything
    # surprising?" triage.
    try:
        with db_conn() as c:
            _ops_mod.write_admin_audit(
                c, "hosts_config_update",
                target_kind="hosts_config",
                target_name=f"{len(saved)} row(s)",
                actor=_u.username or "operator",
                message=f"hosts_config full-replace by {_u.username or 'operator'}: "
                        f"{len(saved)} row(s) persisted",
            )
    except Exception as e:
        print(f"[hosts] config-update audit-row write failed: {e}")
    return {"hosts": saved, "count": len(saved)}


def _sweep_orphan_provider_state_rows(live_ids: set) -> int:
    """Delete `<provider>:<host_id>` rows in `host_failure_state` and
    `host_provider_last_ok` whose suffix isn't in ``live_ids``. Also
    deletes BARE host_id rows whose value isn't in ``live_ids`` (these
    come from the whole-host sampler). ALSO deletes
    per-provider rows where the host EXISTS in ``live_ids`` but the
    provider isn't actually configured on the host's curated row —
    catches orphans like "pulse:apc.example.com" on a host that only has
    Ping enabled (the operator-reported case hardened the
    probe-side gate but didn't clean the DB rows that accumulated
    pre-fix). Returns total rows removed.

    Called by `api_hosts_config_set` after every Save AND from
    `_lifespan` startup so a deploy automatically cleans accumulated
    orphans without operator action.
    """
    total = 0
    if not isinstance(live_ids, set):
        live_ids = set(live_ids or [])
    # Build a per-host "providers configured" map so we can spot orphan
    # provider-prefixed rows. Mirror the same check
    # `_merge_one_host` uses post-fix to decide whether to probe each
    # provider for a given host.
    curated = _load_hosts_config()
    host_providers: dict[str, set] = {}
    for h in curated:
        hid = h.get("id") or ""
        if not hid:
            continue
        configured: set[str] = set()
        if (h.get("beszel_name") or "").strip():
            configured.add("beszel")
        if (h.get("pulse_name") or "").strip():
            configured.add("pulse")
        if (h.get("ne_url") or "").strip():
            configured.add("node_exporter")
        if (h.get("webmin_name") or "").strip():
            configured.add("webmin")
        if bool((h.get("ping") or {}).get("enabled", False)):
            configured.add("ping")
        # SNMP: configured when EITHER `snmp_name` OR the shared
        # `address` field is set AND `snmp.enabled === True`. MUST stay
        # in lock-step with `logic/host_metrics_sampler.py:_host_provider_config`
        # AND the canonical resolver chain (aliases → snmp_name →
        # address → SKIP) used by `_merge_one_host` and the live
        # sampler. Pre-fix the gate required `snmp_name` non-empty,
        # so a host that cleared `snmp_name` intending to use the
        # shared `address` field had its `host_failure_state` +
        # `host_provider_last_ok` rows DELETED by this sweep on every
        # `hosts_config` save AND on every container restart — actively
        # destroying state instead of just skipping it.
        snmp_target_present = (
            (h.get("snmp_name") or "").strip()
            or (h.get("address") or "").strip()
        )
        if snmp_target_present and (
            isinstance(h.get("snmp"), dict) and h["snmp"].get("enabled") is True
        ):
            configured.add("snmp")
        host_providers[hid] = configured
    try:
        with db_conn() as c:
            for table in ("host_failure_state", "host_provider_last_ok"):
                rows = c.execute(f"SELECT host_id, provider FROM {table}").fetchall()
                doomed: list[tuple[str, str]] = []
                for r in rows:
                    bare = r[0] or ""
                    provider = r[1] or ""
                    if not bare:
                        continue
                    # Whole-host row whose host_id is gone from the curated list.
                    if bare not in live_ids:
                        doomed.append((bare, provider))
                        continue
                    # Per-provider orphan: host still curated but the
                    # provider isn't actually configured on its row.
                    # Most common path is the pre-fix fall-through that
                    # probed Pulse/Beszel against `host.id` for hosts
                    # without the corresponding alias set. Skip whole-
                    # host rows (provider='') and unknown providers.
                    if (
                        provider
                        and provider in _PROVIDER_AUTO_PAUSE_NAMES
                        and bare in host_providers
                        and provider not in host_providers[bare]
                    ):
                        doomed.append((bare, provider))
                if doomed:
                    c.executemany(
                        f"DELETE FROM {table} WHERE host_id = ? AND provider = ?",
                        doomed,
                    )
                    total += len(doomed)
                    print(f"[hosts] orphan sweep: removed {len(doomed)} row(s) from {table}")
    except Exception as e:
        print(f"[hosts] orphan sweep DB error: {e}")
    return total


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.post("/api/hosts/{host_id}/resume-sampling")
async def api_hosts_resume_sampling(
    host_id: str,
    _u: AdminUser,
):
    """Admin-only: clear the auto-pause marker for a host that the
    host_metrics_sampler has put on hold after consecutive failures.
    Next sampler tick will re-attempt the probe; if it succeeds the
    row stays cleared, if it fails the failure-window counter starts
    again from zero.

    Validates ``host_id`` against the curated ``hosts_config`` list
    so the endpoint behaves consistently with `/api/hosts/one/{host_id}`
    — admin previously
    could DELETE a stale failure-state row for a host_id that wasn't
    even in the curated list, which is harmless but inconsistent with
    the parallel endpoint's 404).
    """
    curated = _load_hosts_config()
    h = next((x for x in curated if x.get("id") == host_id), None)
    if h is None:
        raise HTTPException(404, f"Host not found: {host_id}")
    try:
        with db_conn() as c:
            # Whole-host pause lives in the bare-id row (provider='').
            # Per-provider rows (provider='<name>') stay so individual
            # provider chips keep their state — operator clears those via
            # /api/hosts/{id}/provider/{name}/resume.
            cur = c.execute(
                "DELETE FROM host_failure_state WHERE host_id = ? AND provider = ''",
                (host_id,),
            )
            cleared = cur.rowcount or 0
            # Append-only transition log so the host-drawer Timeline
            # surfaces the manual-resume event alongside the automatic
            # paused / recovered transitions the sampler writes. Same
            # `recovered` kind (frontend already renders it green +
            # check icon); actor field carries the user's username so
            # the timeline distinguishes "sampler auto-cleared" from
            # "user X clicked Resume" when present.
            if cleared:
                try:
                    c.execute(
                        "INSERT INTO host_failure_events "
                        "(ts, host_id, provider, kind, error, actor) "
                        "VALUES (?, ?, '', 'recovered', NULL, ?)",
                        (time.time(), host_id, _u.username or "operator"),
                    )
                except Exception as ev_err:
                    print(f"[hosts] resume-sampling: failure-event log write failed: {ev_err}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"resume-sampling failed: {e}")
    # also clear the SSH + Webmin auth cooldowns for
    # this host so a single resume click recovers from the
    # all-three-providers-paused-on-same-host case (sampler is paused,
    # SSH cooldown still arming, Webmin cooldown still arming). Each
    # cooldown is keyed differently — SSH on (host_id, user); Webmin
    # on (base_url, user). For Webmin we walk the per-host alias map +
    # the global URL since either could be the cooldown target. Both
    # provider modules expose `_auth_cooldown_timer` per CLAUDE.md's
    # "Add a host-stats provider" canonical checklist.
    cooldown_cleared: list[str] = []
    try:
        from logic import ssh as _ssh
        # SSH cooldowns are keyed on (host_id, user); we don't know the
        # user here, so wipe the entire cooldown map for this host_id
        # by iterating known users from the cooldown's internal store.
        # Cooldown.clear(*key) takes the same key tuple as arm/remaining
        # so we walk and clear known (host_id, *) pairs. Implementation
        # detail: Cooldown stores keys as a tuple in `._timers`.
        timers = getattr(_ssh.auth_cooldown_timer, "_armed", None)
        if timers is not None:
            doomed = [k for k in list(timers.keys())
                      if isinstance(k, tuple) and k and k[0] == (host_id or "")]
            for k in doomed:
                _ssh.auth_cooldown_timer.clear(*k)
                cooldown_cleared.append(f"ssh:{k}")
    except Exception as e:
        print(f"[hosts] resume-sampling: ssh cooldown clear failed: {e}")
    try:
        from logic import webmin as _webmin
        # Webmin cooldowns key on (base_url, user). The host's base_url
        # could come from `webmin_url` field or the alias map; walk the
        # cooldown's `_timers` dict and drop any entry whose first key
        # element matches one of the candidate URLs.
        candidates: set[str] = set()
        wurl = (h.get("webmin_url") or "").strip().rstrip("/")
        if wurl:
            candidates.add(wurl)
        # Resolved URL via the alias map — Webmin module's helper
        webmin_name = (h.get("webmin_name") or "").strip()
        if webmin_name:
            try:
                aliases_raw = get_setting(Settings.WEBMIN_ALIASES) or ""
                aliases = json.loads(aliases_raw) if aliases_raw else {}
                if isinstance(aliases, dict):
                    aliased = (aliases.get(webmin_name) or "").strip().rstrip("/")
                    if aliased:
                        candidates.add(aliased)
            except (json.JSONDecodeError, ValueError, AttributeError):
                pass
        timers = getattr(_webmin.auth_cooldown_timer, "_armed", None)
        if timers is not None and candidates:
            doomed = [k for k in list(timers.keys())
                      if isinstance(k, tuple) and k and k[0] in candidates]
            for k in doomed:
                _webmin.auth_cooldown_timer.clear(*k)
                cooldown_cleared.append(f"webmin:{k}")
    except Exception as e:
        print(f"[hosts] resume-sampling: webmin cooldown clear failed: {e}")
    if cooldown_cleared:
        print(f"[hosts] {host_id!r} resume-sampling cleared cooldowns: {cooldown_cleared}")
    invalidate_host_provider_cache()
    # Admin audit row — operator-initiated unpause is an audit event
    # even though the matching auto-pause was a sampler-driven write.
    try:
        with db_conn() as c:
            _ops_mod.write_admin_audit(
                c, "host_resume_sampling",
                target_kind="host", target_name=host_id, target_id=host_id,
                actor=_u.username or "operator",
                message=(f"host sampling resumed by {_u.username or 'operator'}; "
                         f"cleared={bool(cleared)} cooldowns_cleared={len(cooldown_cleared)}"),
            )
    except Exception as e:
        print(f"[hosts] resume-sampling: audit-row write failed: {e}")
    return {
        "host_id": host_id,
        "cleared": bool(cleared),
        "cooldowns_cleared": len(cooldown_cleared),
    }


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.post("/api/hosts/{host_id}/provider/{provider}/resume")
async def api_hosts_provider_resume(
    host_id: str, provider: str,
    request: Request,
    _u: AdminUser,
):
    """Admin-only: clear the per-(provider, host) auto-pause marker
    . Mirrors `/api/hosts/{id}/resume-sampling` but scoped to a
    single provider — the host-level pause stays intact, only the
    `<provider>:<host_id>` row is removed.

    Also clears the provider's in-memory cool-down for this host so the
    next probe runs immediately rather than waiting out the cool-down
    window. Without that the operator's "Resume" click would technically
    succeed (the pause is cleared) but the very next probe would short-
    circuit on the unrelated cool-down — unintuitive.

    Validates ``provider`` against the supported set so an unknown
    provider name returns 400 instead of silently no-op'ing.
    """
    provider = (provider or "").strip().lower()
    if provider not in _PROVIDER_AUTO_PAUSE_NAMES:
        raise HTTPException(400, f"Unsupported provider: {provider}")
    curated = _load_hosts_config()
    h = next((x for x in curated if x.get("id") == host_id), None)
    if h is None:
        raise HTTPException(404, f"Host not found: {host_id}")
    try:
        with db_conn() as c:
            cur = c.execute(
                "DELETE FROM host_failure_state "
                "WHERE host_id = ? AND provider = ?",
                (host_id, provider),
            )
            cleared = cur.rowcount or 0
            # Append-only transition log so the host-drawer Timeline
            # surfaces this manual-resume event. Mirrors the whole-host
            # resume endpoint's logic; actor=username distinguishes from
            # the sampler's automatic recovery writes.
            if cleared:
                try:
                    c.execute(
                        "INSERT INTO host_failure_events "
                        "(ts, host_id, provider, kind, error, actor) "
                        "VALUES (?, ?, ?, 'recovered', NULL, ?)",
                        (time.time(), host_id, provider, _u.username or "operator"),
                    )
                except Exception as ev_err:
                    print(f"[hosts] provider/{provider}/resume: failure-event log write failed: {ev_err}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"resume failed: {e}")
    # Provider-specific cool-down clears so the next probe doesn't hit
    # an unrelated short throttle. SNMP cool-down is keyed on the
    # SNMP target (alias > snmp_name); Webmin on (base_url, user).
    cooldown_cleared: list[str] = []
    if provider == "snmp":
        try:
            from logic import snmp as _snmp
            timers = getattr(_snmp.unreachable_cooldown, "_armed", None)
            if timers is not None:
                # Resolve the SNMP target the same way `_merge_one_host`
                # does: alias map > row's snmp_name. Whichever matches
                # the cool-down key gets cleared.
                aliases_raw = get_setting(Settings.SNMP_ALIASES) or ""
                try:
                    aliases = json.loads(aliases_raw) if aliases_raw else {}
                except (json.JSONDecodeError, ValueError):
                    aliases = {}
                candidates: set[str] = set()
                if isinstance(aliases, dict) and aliases.get(host_id):
                    candidates.add(str(aliases[host_id]).strip())
                snmp_name = (h.get("snmp_name") or "").strip()
                if snmp_name:
                    candidates.add(snmp_name)
                doomed = [k for k in list(timers.keys())
                          if isinstance(k, tuple) and k and k[0] in candidates]
                for k in doomed:
                    _snmp.unreachable_cooldown.clear(*k)
                    cooldown_cleared.append(f"snmp:{k}")
        except Exception as e:
            print(f"[hosts] provider/snmp/resume cooldown clear failed: {e}")
        # also drop the per-host SNMP success + fail caches so the next
        # probe in `_merge_one_host` actually hits the wire. Cache keys
        # are (host_id, frozenset|None) tuples — drop every entry whose
        # first slot matches this host.
        for _k in [k for k in list(_snmp_host_cache.keys()) if k and k[0] == host_id]:
            _snmp_host_cache.pop(_k, None)
        for _k in [k for k in list(_snmp_host_fail_cache.keys()) if k and k[0] == host_id]:
            _snmp_host_fail_cache.pop(_k, None)
    elif provider == "webmin":
        try:
            from logic import webmin as _webmin
            candidates: set[str] = set()
            wurl = (h.get("webmin_url") or "").strip().rstrip("/")
            if wurl:
                candidates.add(wurl)
            webmin_name = (h.get("webmin_name") or "").strip()
            if webmin_name:
                try:
                    aliases_raw = get_setting(Settings.WEBMIN_ALIASES) or ""
                    aliases = json.loads(aliases_raw) if aliases_raw else {}
                    if isinstance(aliases, dict):
                        aliased = (aliases.get(webmin_name) or "").strip().rstrip("/")
                        if aliased:
                            candidates.add(aliased)
                except (json.JSONDecodeError, ValueError, AttributeError):
                    pass
            timers = getattr(_webmin.auth_cooldown_timer, "_armed", None)
            if timers is not None and candidates:
                doomed = [k for k in list(timers.keys())
                          if isinstance(k, tuple) and k and k[0] in candidates]
                for k in doomed:
                    _webmin.auth_cooldown_timer.clear(*k)
                    cooldown_cleared.append(f"webmin:{k}")
        except Exception as e:
            print(f"[hosts] provider/webmin/resume cooldown clear failed: {e}")
        _webmin_host_cache.pop(host_id, None)
        _webmin_host_fail_cache.pop(host_id, None)
    elif provider == "http_probe":
        # HTTP probe has no auth-cooldown timer (the probe targets are
        # operator-curated URLs, not credentialed APIs — 401 / 403
        # are just status outcomes, not lockout signals). The only
        # state to bust is the per-host success / failure cache so the
        # next probe in `_merge_one_host` reaches `populate_host_http_merge`
        # immediately rather than serving up to
        # `tuning_http_probe_host_cache_ttl_seconds` of stale data.
        _http_probe_host_cache.pop(host_id, None)
    elif provider == "ping":
        # Ping cool-down keyed `(host_clean, port_int)` per-host. Walk
        # the timer's `_armed` map and drop any entry whose first key
        # element matches the host's reachable target (host field +
        # any per-host ping config).
        try:
            from logic import ping as _ping
            timers = getattr(_ping.unreachable_cooldown, "_armed", None)
            if timers is not None:
                # Resolve candidate targets — `host_id` matches what
                # the sampler passes, but operators may also configure
                # a different host-field target via `hosts_config[].ping`.
                candidates: set[str] = {host_id}
                pcfg = h.get("ping") or {}
                if isinstance(pcfg, dict):
                    target = (pcfg.get("host") or "").strip()
                    if target:
                        candidates.add(target)
                doomed = [k for k in list(timers.keys())
                          if isinstance(k, tuple) and k and k[0] in candidates]
                for k in doomed:
                    _ping.unreachable_cooldown.clear(*k)
                    cooldown_cleared.append(f"ping:{k}")
        except Exception as e:
            print(f"[hosts] provider/ping/resume cooldown clear failed: {e}")
    # beszel / pulse / node_exporter: no per-host in-memory cool-down
    # to clear. The DB row delete above is the entire reset — next
    # tick re-attempts the probe.
    if cooldown_cleared:
        print(f"[hosts] {host_id!r} provider/{provider}/resume cleared "
              f"cooldowns: {cooldown_cleared}")
    invalidate_host_provider_cache()
    _invalidate_provider_state_cache()  # ensure /api/hosts/list reflects the resume immediately
    # SSE: surface the resume so the SPA's chip flips back to its
    # default state without waiting for the next poll cycle.
    try:
        _events.publish(
            "host:failure_state_changed",
            {
                "host_id": host_id,
                "provider": provider,
                "paused": False,
                "cleared": True,
            },
            client_id=_request_client_id(request),
        )
    except Exception as ee:
        print(f"[events] host:failure_state_changed publish failed: {ee}")
    # Admin audit row — per-(provider, host) resume is an operator-
    # initiated event. The matching auto-pause was a sampler write so it
    # isn't audited; the resume IS.
    try:
        with db_conn() as c:
            _ops_mod.write_admin_audit(
                c, "host_provider_resume",
                target_kind="host",
                target_name=f"{provider}:{host_id}", target_id=host_id,
                actor=_u.username or "operator",
                message=(f"{provider} resume on host {host_id} by {_u.username or 'operator'}; "
                         f"cleared={bool(cleared)} cooldowns_cleared={len(cooldown_cleared)}"),
            )
    except Exception as e:
        print(f"[hosts] provider/{provider}/resume: audit-row write failed: {e}")
    return {
        "host_id": host_id,
        "provider": provider,
        "cleared": bool(cleared),
        "cooldowns_cleared": len(cooldown_cleared),
    }


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.post("/api/hosts/test")
async def api_hosts_test(
    body: dict,
    _u: AdminUser,
):
    """Admin-only: probe each provider for a single host-config row.

    Body: ``{beszel_name, pulse_name, ne_url}`` — any field blank is
    skipped. Returns ``{beszel: {ok, detail}, pulse: {...},
    node_exporter: {...}}`` with per-provider pass/fail + a short
    description the UI shows beside the row.

    Shares probes with the live Hosts-view code path so a pass here
    guarantees the main page will render data for this host.
    """
    from logic import beszel as _beszel
    from logic import pulse as _pulse
    from logic import node_exporter as _ne
    from logic import webmin as _webmin
    from logic import snmp as _snmp

    beszel_name = (body.get("beszel_name") or "").strip()
    pulse_name = (body.get("pulse_name") or "").strip()
    ne_url = (body.get("ne_url") or "").strip()
    # webmin_url takes precedence over the row's webmin_aliases entry —
    # per-row test fields beat global settings, same pattern as ne_url.
    webmin_url = (body.get("webmin_url") or "").strip().rstrip("/")
    row_id = (body.get("host_id") or "").strip()
    if not webmin_url and row_id:
        try:
            aliases = json.loads(get_setting(Settings.WEBMIN_ALIASES, "{}") or "{}")
            if isinstance(aliases, dict):
                webmin_url = str(aliases.get(row_id, "") or "").strip().rstrip("/")
        except ValueError:
            webmin_url = ""
    # SNMP test row. Body fields are all optional; defaults flow
    # through from the global settings the same way other providers do.
    snmp_target = (body.get("snmp_target") or body.get("snmp_name") or "").strip()
    if not snmp_target and row_id:
        try:
            sn_aliases = json.loads(get_setting(Settings.SNMP_ALIASES, "{}") or "{}")
            if isinstance(sn_aliases, dict):
                snmp_target = str(sn_aliases.get(row_id, "") or "").strip()
        except ValueError:
            snmp_target = ""
    # Mirror the live resolver chain — fall through to the curated
    # `address` field when neither body.snmp_target/_name nor the alias
    # map produced a target. Without this, an SNMP-enabled host that
    # left `snmp_name` blank in favour of the shared `address` field
    # tested as "skipped (not set)" even though the live sampler /
    # `_merge_one_host` chain probes it correctly via `address`. Only
    # consult the curated row when the body explicitly didn't include
    # an `address` key — operators editing a row with `address`
    # cleared in the UI shouldn't see the OLD persisted address used
    # as the test target.
    if not snmp_target and row_id:
        body_address = (body.get("address") or "").strip()
        if body_address:
            snmp_target = body_address
        else:
            try:
                curated = _load_hosts_config()
                row = next((r for r in curated if r.get("id") == row_id), None)
                if row:
                    snmp_target = (row.get("address") or "").strip()
            except (json.JSONDecodeError, ValueError, OSError):
                pass
    snmp_community = (body.get("snmp_community") or "").strip()
    snmp_version = (body.get("snmp_version") or "").strip().lower()
    try:
        snmp_port = int(body.get("snmp_port") or 0) or 0
    except (TypeError, ValueError):
        snmp_port = 0
    out = {
        "beszel": {"ok": False, "skipped": True, "detail": "not set"},
        "pulse": {"ok": False, "skipped": True, "detail": "not set"},
        "node_exporter": {"ok": False, "skipped": True, "detail": "not set"},
        "webmin": {"ok": False, "skipped": True, "detail": "not set"},
        "snmp": {"ok": False, "skipped": True, "detail": "not set"},
        # Ping + HTTP probe slots — the SPA's test-result render
        # iterates a 7-element list ['beszel', ..., 'http_probe'].
        # Slots that aren't initialised render as `undefined` and the
        # x-show="!skipped" gate falls through to "hide" leaving the
        # operator with an empty result block. Default both to skipped
        # so the SPA can correctly render "not tested" + the actual
        # probe blocks below override per body fields.
        "ping": {"ok": False, "skipped": True, "detail": "not set"},
        "http_probe": {"ok": False, "skipped": True, "detail": "not set"},
    }

    # Respect the global host_stats_source CSV — a provider disabled
    # in Settings → Host stats MUST NOT be probed here, even if the
    # operator filled in its per-row field. The live Hosts-view code
    # path already honours this; the per-row test needs to match so
    # "passes here" = "works in production".
    active_sources = {
        s.strip().lower() for s in
        (get_setting(Settings.HOST_STATS_SOURCE) or "").split(",")
        if s.strip() and s.strip().lower() != "none"
    }

    if beszel_name and "beszel" not in active_sources:
        out["beszel"] = {"ok": False, "skipped": True,
                         "detail": "disabled in host_stats_source"}
        beszel_name = ""  # skip the probe block below
    if pulse_name and "pulse" not in active_sources:
        out["pulse"] = {"ok": False, "skipped": True,
                        "detail": "disabled in host_stats_source"}
        pulse_name = ""
    if ne_url and "node_exporter" not in active_sources:
        out["node_exporter"] = {"ok": False, "skipped": True,
                                "detail": "disabled in host_stats_source"}
        ne_url = ""
    if webmin_url and "webmin" not in active_sources:
        out["webmin"] = {"ok": False, "skipped": True,
                         "detail": "disabled in host_stats_source"}
        webmin_url = ""
    if snmp_target and "snmp" not in active_sources:
        out["snmp"] = {"ok": False, "skipped": True,
                       "detail": "disabled in host_stats_source"}
        snmp_target = ""
    # Per-host SNMP opt-in gate. Even when SNMP is enabled globally,
    # individual rows can leave `snmp.enabled = false` so the sampler
    # doesn't probe them. The test endpoint must honour the same gate
    # — otherwise the "Test providers" button against a host with
    # SNMP disabled fires a real SNMP probe (50s walk budget) and
    # returns a timeout that misleads the operator into thinking the
    # row is mis-configured.
    if snmp_target and row_id:
        try:
            curated = _load_hosts_config()
            row = next((r for r in curated if r.get("id") == row_id), None)
            if row is not None:
                per_host_snmp = row.get("snmp")
                if isinstance(per_host_snmp, dict) and per_host_snmp.get("enabled") is not True:
                    out["snmp"] = {
                        "ok": False, "skipped": True,
                        "detail": "per-host SNMP disabled",
                    }
                    snmp_target = ""
        except (json.JSONDecodeError, ValueError, OSError):
            pass  # fall through; gate is best-effort

    if beszel_name:
        hub_url = get_setting(Settings.BESZEL_HUB_URL) or ""
        ident = get_setting(Settings.BESZEL_IDENTITY) or ""
        passw = get_setting(Settings.BESZEL_PASSWORD) or ""
        verify = (get_setting(Settings.BESZEL_VERIFY_TLS, "true") or "true").lower() == "true"
        if hub_url and ident and passw:
            r = await _beszel.probe_hub(hub_url, ident, passw, verify_tls=verify)
            if r.get("error"):
                out["beszel"] = {"ok": False, "skipped": False,
                                 "detail": f"hub error: {r['error']}"}
            elif beszel_name in (r.get("systems") or {}):
                st = r["systems"][beszel_name]
                mem = st.get("host_mem_total") or 0
                disk = st.get("host_disk_total") or 0
                out["beszel"] = {
                    "ok": True, "skipped": False,
                    "detail": (
                        f"matched · mem={mem // (1024 ** 3) if mem else '?'} GB · "
                        f"disk={disk // (1024 ** 3) if disk else '?'} GB"
                    ),
                }
            else:
                names = sorted((r.get("systems") or {}).keys(), key=str.lower)
                hint = ", ".join(names[:3])
                if len(names) > 3:
                    hint += f" (+{len(names) - 3} more)"
                out["beszel"] = {"ok": False, "skipped": False,
                                 "detail": f"no match in hub. Known: {hint or 'none'}"}
        else:
            out["beszel"] = {"ok": False, "skipped": False,
                             "detail": "Beszel creds not configured"}

    if pulse_name:
        pulse_url = get_setting(Settings.PULSE_URL) or ""
        pulse_tok = get_setting(Settings.PULSE_TOKEN) or ""
        verify = (get_setting(Settings.PULSE_VERIFY_TLS, "true") or "true").lower() == "true"
        if pulse_url and pulse_tok:
            r = await _pulse.probe_pulse(pulse_url, pulse_tok, verify_tls=verify)
            if r.get("error"):
                out["pulse"] = {"ok": False, "skipped": False,
                                "detail": f"pulse error: {r['error']}"}
                st = None
            else:
                st = _pulse.lookup(r.get("hosts") or {}, pulse_name)
            if st is not None:
                kind = st.get("pulse_kind") or "host"
                out["pulse"] = {"ok": True, "skipped": False,
                                "detail": f"matched ({kind})"}
            elif not r.get("error"):
                names = sorted((r.get("hosts") or {}).keys(), key=str.lower)
                hint = ", ".join(names[:3])
                if len(names) > 3:
                    hint += f" (+{len(names) - 3} more)"
                out["pulse"] = {"ok": False, "skipped": False,
                                "detail": f"no match in Pulse. Known: {hint or 'none'}"}
        else:
            out["pulse"] = {"ok": False, "skipped": False,
                            "detail": "Pulse creds not configured"}

    if ne_url:
        try:
            async with httpx.AsyncClient(verify=False, timeout=8.0) as client:
                stats = await _ne.probe_node(client, ne_url)
        except Exception as e:
            stats = {"exporter_error": str(e)}
        if stats.get("exporter_error"):
            out["node_exporter"] = {"ok": False, "skipped": False,
                                    "detail": stats["exporter_error"]}
        else:
            _mem_raw = stats.get("host_mem_total") or 0
            mem: int = int(_mem_raw) if isinstance(_mem_raw, (int, float)) else 0
            out["node_exporter"] = {
                "ok": True, "skipped": False,
                "detail": f"reachable · mem={mem // (1024 ** 3) if mem else '?'} GB",
            }

    if webmin_url:
        user = get_setting(Settings.WEBMIN_USER) or ""
        passw = get_setting(Settings.WEBMIN_PASSWORD) or ""
        verify = (get_setting(Settings.WEBMIN_VERIFY_TLS, "false") or "false").lower() == "true"
        if not user or not passw:
            out["webmin"] = {"ok": False, "skipped": False,
                             "detail": "Webmin creds not configured"}
        else:
            r = await _webmin.probe_webmin(
                webmin_url, user, passw, verify_tls=verify, timeout=8.0,
            )
            if r.get("error") and not r.get("hosts"):
                out["webmin"] = {"ok": False, "skipped": False,
                                 "detail": f"webmin error: {r['error']}"}
            elif r.get("hosts"):
                host_key, stats = next(iter(r["hosts"].items()))
                pending = stats.get("host_updates_pending") or 0
                security = stats.get("host_updates_security") or 0
                detail = (f"matched · {host_key} · "
                          f"{pending} updates ({security} sec)")
                if r.get("partial_errors"):
                    detail += f" · {len(r['partial_errors'])} module(s) failed"
                out["webmin"] = {"ok": True, "skipped": False, "detail": detail}
            else:
                out["webmin"] = {"ok": False, "skipped": False,
                                 "detail": "webmin responded with no parseable host"}

    if snmp_target:
        if not _snmp.has_snmp_support():
            out["snmp"] = {
                "ok": False, "skipped": False,
                "detail": "pysnmp not installed (pip install pysnmp)",
            }
        else:
            community = snmp_community or get_setting(Settings.SNMP_DEFAULT_COMMUNITY, "public") or "public"
            version = snmp_version or (get_setting(Settings.SNMP_DEFAULT_VERSION, "v2c") or "v2c").lower()
            try:
                port = snmp_port or tuning.tuning_int(Tunable.SNMP_DEFAULT_PORT)
            except (TypeError, ValueError):
                port = 161
            # Per-host overrides forwarded from the SPA's testHostRow
            # so the per-row Test mirrors the live probe path. Pre-fix
            # this only honoured target+community+version+port and
            # silently used the GLOBAL walk_concurrency / wall_clock
            # tunables — which surfaced as "Test failed: HTTP 504"
            # for slow iDRAC chassis where the operator's 120s global
            # budget exceeded NPM's proxy_read_timeout. Now: per-host
            # walk_concurrency / vendors honoured; per-host
            # wall_clock_budget capped at 50s (NPM proxy ceiling).
            v3_user_t = (body.get("snmp_v3_user") or "").strip() or (get_setting(Settings.SNMP_V3_USER) or "")
            v3_auth_t = (body.get("snmp_v3_auth_key") or "").strip() or (get_setting(Settings.SNMP_V3_AUTH_KEY) or "")
            v3_priv_t = (body.get("snmp_v3_priv_key") or "").strip() or (get_setting(Settings.SNMP_V3_PRIV_KEY) or "")
            try:
                walk_conc_t = int(body.get("snmp_walk_concurrency") or 0) or None
            except (TypeError, ValueError):
                walk_conc_t = None
            try:
                wcb_t_raw = float(body.get("snmp_wall_clock_budget") or 0) or None
            except (TypeError, ValueError):
                wcb_t_raw = None
            _ROW_TEST_BUDGET_CAP = 50.0
            wcb_t = (
                min(_ROW_TEST_BUDGET_CAP, wcb_t_raw)
                if wcb_t_raw else _ROW_TEST_BUDGET_CAP
            )
            vendors_t = _clean_vendors_input(body.get("snmp_vendors"))
            r = await _snmp.probe_snmp(
                snmp_target,
                community=community,
                version=version,
                port=port,
                v3_user=v3_user_t,
                v3_auth_key=v3_auth_t,
                v3_priv_key=v3_priv_t,
                walk_concurrency=walk_conc_t,
                vendors=vendors_t,
                wall_clock_budget=wcb_t,
                # Operator clicked Test — bypass the unreachable
                # cool-down so a recent failure doesn't gate the smoke
                # probe.
                bypass_cooldown=True,
            )
            if r.get("error") and not r.get("hosts"):
                out["snmp"] = {"ok": False, "skipped": False,
                               "detail": f"snmp error: {r['error']}"}
            elif r.get("hosts"):
                host_key, stats = next(iter(r["hosts"].items()))
                cpu = stats.get("host_cpu_percent")
                mem = stats.get("host_mem_total") or 0
                detail_bits = [f"matched · {host_key}"]
                if cpu is not None:
                    try:
                        detail_bits.append(f"cpu={int(cpu)}%")
                    except (TypeError, ValueError):
                        pass
                if mem:
                    detail_bits.append(f"mem={mem // (1024 ** 3)} GB")
                out["snmp"] = {
                    "ok": True, "skipped": False,
                    "detail": " · ".join(detail_bits),
                }
            else:
                out["snmp"] = {"ok": False, "skipped": False,
                               "detail": "snmp responded with no parseable data"}

    # Ping — fires when the row carries `ping.enabled = true`. Reuses
    # the same probe_ping path the sampler runs, so "passes here" =
    # "works for the live reachability chart".
    ping_enabled = bool(body.get("ping_enabled"))
    if ping_enabled and "ping" in active_sources:
        try:
            from logic import ping as _ping_mod
            # Target chain mirrors the sampler: per-host `address` →
            # bare host_id fallback. Skip when neither is set.
            ping_target = (body.get("address") or row_id or "").strip()
            if not ping_target:
                out["ping"] = {
                    "ok": False, "skipped": True,
                    "detail": "no probe target (address blank)",
                }
            else:
                try:
                    ping_port = int(body.get("ping_port") or 0)
                except (TypeError, ValueError):
                    ping_port = 0
                r = await _ping_mod.probe_ping(
                    ping_target,
                    port=(ping_port or 443),
                    timeout_seconds=3.0,
                )
                if r.get("alive"):
                    rtt = r.get("rtt_ms")
                    out["ping"] = {
                        "ok": True, "skipped": False,
                        "detail": f"alive · {rtt} ms" if rtt is not None else "alive",
                    }
                else:
                    out["ping"] = {
                        "ok": False, "skipped": False,
                        "detail": str(r.get("error") or "unreachable")[:120],
                    }
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            out["ping"] = {"ok": False, "skipped": False,
                           "detail": f"{type(e).__name__}: {str(e)[:80]}"}
    elif ping_enabled:
        out["ping"] = {"ok": False, "skipped": True,
                       "detail": "disabled in host_stats_source"}

    # HTTP probe — fires when the row carries `http_probe.enabled =
    # true`. Reuses probe_http_health (the sampler's own probe) so the
    # test's pass / fail criteria match production exactly. URL list
    # resolves server-side via the same operator-override → fallback
    # chain the sampler uses. `verify_tls` honours the row's per-host
    # override (default True; operators set False for self-signed
    # homelab certs) so the test matches the sampler exactly.
    http_probe_enabled = bool(body.get("http_probe_enabled"))
    if http_probe_enabled and "http_probe" in active_sources:
        try:
            from logic import http_probe as _http_probe_mod
            urls_raw = body.get("http_probe_urls") or []
            urls: list[str] = []
            if isinstance(urls_raw, list):
                urls = [str(u).strip() for u in urls_raw if str(u or "").strip()]
            # Per-row verify_tls override. Default True; explicit False
            # opts into self-signed-cert acceptance for this host's
            # probe path.
            verify_tls_body = body.get("http_probe_verify_tls")
            row_verify_tls = True if verify_tls_body is None else bool(verify_tls_body)
            # Fallback to top-level url + services[].url when no
            # operator override list was provided.
            if (not urls or verify_tls_body is None) and row_id:
                try:
                    curated_http = _load_hosts_config()
                    row_http = next((r for r in curated_http if r.get("id") == row_id), None)
                    if row_http:
                        if not urls:
                            top_url = (row_http.get("url") or "").strip()
                            if top_url:
                                urls.append(top_url)
                            svcs = row_http.get("services")
                            if isinstance(svcs, list):
                                for svc in svcs:
                                    if isinstance(svc, dict):
                                        su = (svc.get("url") or "").strip()
                                        if su:
                                            urls.append(su)
                        # If the SPA didn't send verify_tls, pull it
                        # from the persisted row's http_probe sub-dict
                        # so the test honours the stored setting.
                        if verify_tls_body is None:
                            persisted = (row_http.get("http_probe") or {})
                            if isinstance(persisted, dict) and "verify_tls" in persisted:
                                row_verify_tls = bool(persisted.get("verify_tls"))
                except (json.JSONDecodeError, ValueError, OSError):
                    pass
            # Dedupe while preserving order. Explicit form avoids the
            # `set.add() in a boolean expression` warning — add returns
            # None which PyCharm flags as "function doesn't return".
            seen_urls: set[str] = set()
            deduped: list[str] = []
            for u in urls:
                if u not in seen_urls:
                    seen_urls.add(u)
                    deduped.append(u)
            urls = deduped
            if not urls:
                out["http_probe"] = {
                    "ok": False, "skipped": True,
                    "detail": "no probe URLs (set http_probe.urls or top-level url)",
                }
            else:
                results = []
                for u in urls[:8]:  # cap test fan-out at 8 URLs
                    rr = await _http_probe_mod.probe_http_health(
                        u, timeout=8.0, dns_timeout=5.0,
                        verify_tls=row_verify_tls,
                    )
                    results.append(rr)
                ok_count = sum(1 for rr in results if rr.get("ok"))
                if ok_count == len(results):
                    avg_lat = sum((rr.get("latency_ms") or 0) for rr in results) // max(1, len(results))
                    out["http_probe"] = {
                        "ok": True, "skipped": False,
                        "detail": f"{ok_count}/{len(results)} URLs healthy · avg {avg_lat} ms",
                    }
                elif ok_count > 0:
                    out["http_probe"] = {
                        "ok": False, "skipped": False,
                        "detail": f"{ok_count}/{len(results)} URLs healthy — partial",
                    }
                else:
                    first_err = ""
                    for rr in results:
                        if rr.get("error"):
                            first_err = str(rr.get("error"))[:100]
                            break
                    out["http_probe"] = {
                        "ok": False, "skipped": False,
                        "detail": first_err or f"0/{len(results)} URLs healthy",
                    }
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            out["http_probe"] = {"ok": False, "skipped": False,
                                 "detail": f"{type(e).__name__}: {str(e)[:80]}"}
    elif http_probe_enabled:
        out["http_probe"] = {"ok": False, "skipped": True,
                             "detail": "disabled in host_stats_source"}

    return out


@app.get("/api/hosts/discover")
async def api_hosts_discover(_u: AdminUser):
    """Admin-only: pull every known host name from each enabled
    provider. Used by the Admin → Hosts editor as autocomplete source
    so operators don't have to type provider-side names from memory.

    Returns ``{beszel: [names], pulse: [names], errors: {...}}``. Empty
    lists mean either the provider is disabled or its credentials
    aren't set — the UI treats both the same.
    """
    from logic import beszel as _beszel
    from logic import pulse as _pulse
    errors: dict[str, str] = {}

    beszel_names: list[str] = []
    hub_url = get_setting(Settings.BESZEL_HUB_URL) or ""
    b_id = get_setting(Settings.BESZEL_IDENTITY) or ""
    b_pw = get_setting(Settings.BESZEL_PASSWORD) or ""
    if hub_url and b_id and b_pw:
        verify = (get_setting(Settings.BESZEL_VERIFY_TLS, "true") or "true").lower() == "true"
        r = await _beszel.probe_hub(hub_url, b_id, b_pw, verify_tls=verify)
        if r.get("error"):
            errors["beszel"] = r["error"]
        else:
            beszel_names = sorted((r.get("systems") or {}).keys(), key=str.lower)

    pulse_names: list[str] = []
    pulse_url = get_setting(Settings.PULSE_URL) or ""
    pulse_tok = get_setting(Settings.PULSE_TOKEN) or ""
    if pulse_url and pulse_tok:
        verify = (get_setting(Settings.PULSE_VERIFY_TLS, "true") or "true").lower() == "true"
        r = await _pulse.probe_pulse(pulse_url, pulse_tok, verify_tls=verify)
        if r.get("error"):
            errors["pulse"] = r["error"]
        else:
            pulse_names = sorted((r.get("hosts") or {}).keys(), key=str.lower)

    # Webmin discovery — one Miniserv per host, so instead of a flat
    # list of names we surface the URL → extracted-hostname map from
    # ``webmin_aliases``. Each alias URL gets probed once so the
    # hostname returned by the target's system-status module can be
    # offered as the ``webmin_name`` autocomplete value.
    webmin_names: list[str] = []
    try:
        wm_aliases_raw = json.loads(get_setting(Settings.WEBMIN_ALIASES, "{}") or "{}")
    except ValueError:
        wm_aliases_raw = {}
    wm_urls = (
        sorted({str(v).strip().rstrip("/")
                for v in wm_aliases_raw.values() if str(v).strip()})
        if isinstance(wm_aliases_raw, dict) else []
    )
    wm_user = get_setting(Settings.WEBMIN_USER) or ""
    wm_pass = get_setting(Settings.WEBMIN_PASSWORD) or ""
    if wm_urls and wm_user and wm_pass:
        from logic import webmin as _webmin
        verify = (get_setting(Settings.WEBMIN_VERIFY_TLS, "false") or "false").lower() == "true"
        wm_results = await asyncio.gather(*(
            _webmin.probe_webmin(u, wm_user, wm_pass, verify_tls=verify,
                                 timeout=8.0)
            for u in wm_urls
        ))
        seen: set[str] = set()
        failed = 0
        for r in wm_results:
            if r.get("hosts"):
                for k in r["hosts"]:
                    if k:
                        seen.add(k)
            elif r.get("error"):
                failed += 1
        webmin_names = sorted(seen, key=str.lower)
        if failed and not webmin_names:
            errors["webmin"] = f"{failed} Webmin URL(s) failed to probe"

    # SNMP discovery — there's no central hub to enumerate so
    # discovery surfaces the configured ``snmp_aliases`` map's keys.
    # Each entry is the curated row's id; the autocomplete value is
    # the alias's TARGET (the SNMP-reachable host/IP). The Admin →
    # Hosts editor renders this list as the snmp_name column's
    # datalist so operators don't have to retype targets they've
    # already mapped at the global level. Empty when no aliases are
    # configured — that's the expected state on first-boot deploys.
    snmp_names: list[str] = []
    try:
        sn_aliases_raw = json.loads(get_setting(Settings.SNMP_ALIASES, "{}") or "{}")
        if isinstance(sn_aliases_raw, dict):
            snmp_names = sorted(
                {str(v).strip() for v in sn_aliases_raw.values() if str(v).strip()},
                key=str.lower,
            )
    except ValueError:
        snmp_names = []

    # HTTP probe discovery — there's no provider-side enumeration to
    # query. Surface every URL we know about across the curated host
    # list (top-level ``url`` + every ``services[].url``) so the Admin
    # → Hosts editor's per-row URL textarea can autocomplete from URLs
    # the operator has already mapped on OTHER rows. Plus the alias
    # CSV's values (for `http_probe_aliases`).
    http_probe_urls: list[str] = []
    try:
        curated_hosts_for_disc = _load_hosts_config()
        url_set: set[str] = set()
        for h_row in curated_hosts_for_disc:
            top_url = (h_row.get("url") or "").strip()
            if top_url:
                url_set.add(top_url)
            svcs = h_row.get("services")
            if isinstance(svcs, list):
                for svc in svcs:
                    if isinstance(svc, dict):
                        u = (svc.get("url") or "").strip()
                        if u:
                            url_set.add(u)
        # Aliases CSV — extract every probe URL the operator has set
        # as an alias target so it shows up in the autocomplete.
        alias_csv = (get_setting(Settings.HTTP_PROBE_ALIASES) or "").strip()
        for token in alias_csv.split(","):
            token = token.strip()
            if "=" in token:
                _, _, v = token.partition("=")
                v = v.strip()
                if v:
                    url_set.add(v)
        http_probe_urls = sorted(url_set, key=str.lower)
    except (ValueError, TypeError) as e:
        errors["http_probe"] = f"discovery error: {e}"

    return {
        "beszel": beszel_names,
        "pulse": pulse_names,
        "webmin": webmin_names,
        "snmp": snmp_names,
        "http_probe": http_probe_urls,
        "errors": errors,
    }


def _item_samples_in_window(item_id: str, since_hours: int) -> dict:
    """Count stats_samples rows for ONE item within a sliding window.

    Returns the same shape the host-debug panel uses for the
    `samples_in_window` block so the SPA can render the two
    diagnostics identically.
    """
    # Defensive — `tuning_int` clamps to (lo, hi) per the resolver so a
    # corrupt DB row can't OOB, but a fully-broken DB state could still
    # raise. The debug panel must stay resilient even when the rest of
    # OmniGrid is down — that's its job. Fall back to the default
    # value baked into TUNABLES (180s for the stats sampler) so the
    # endpoint returns a sensible-shape payload instead of 500-ing.
    try:
        expected_interval = tuning.tuning_int(Tunable.STATS_SAMPLE_INTERVAL_SECONDS)
    except (ValueError, TypeError, KeyError):
        expected_interval = 180
    out: dict[str, Any] = {
        "hours": int(max(1, since_hours)),
        "count": 0,
        "expected_interval_s": expected_interval,
    }
    if not item_id:
        return out
    now = int(time.time())
    cutoff = now - out["hours"] * 3600
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT ts FROM stats_samples "
                "WHERE item_id = ? AND ts >= ? ORDER BY ts ASC",
                (item_id, cutoff),
            ).fetchall()
    except Exception as e:
        out["error"] = str(e)
        return out
    timestamps = [int(r[0]) for r in rows]
    out["count"] = len(timestamps)
    if timestamps:
        out["oldest_ts"] = timestamps[0]
        out["newest_ts"] = timestamps[-1]
        out["newest_age_s"] = now - timestamps[-1]
        if len(timestamps) >= 2:
            gaps = [timestamps[i + 1] - timestamps[i]
                    for i in range(len(timestamps) - 1)]
            gaps.sort()
            out["median_gap_s"] = gaps[len(gaps) // 2]
    return out


# noinspection PyShadowingBuiltins,PyTypeChecker,PyUnresolvedReferences
@app.get("/api/debug/subject")
async def api_debug_subject(
    kind: str = "",
    id: str = "",
    since_hours: int = 1,
    *,
    _u: AdminUser,
):
    """Admin-only diagnostic for the Stacks / Services / Nodes drawers.

    `kind=item` resolves against `_cache["items"]` (covers services,
    standalone containers, orphans).
    `kind=stack` resolves against `_cache["stacks"]` for the rollup
    view — surfaces the stack's item list + aggregate counts +
    per-stack diagnostic flags directly, without the legacy
    items-cache prefix-match quirk.
    `kind=node` resolves against the Swarm node map.

    Returns a JSON dump of the cached record + the per-item stats
    snapshot + a `samples_in_window` block (same shape as
    /api/hosts/debug for diagnostic-rendering reuse) + a
    human-readable `diagnostics` list explaining why drawer charts
    might be showing "Collecting data" or empty bars.

    Lightweight by design — no fresh probes against Portainer or
    providers. Reads the same `_cache` / `_stats_cache` / DB tables
    the live drawer already consumes.
    """
    if kind not in ("item", "stack", "node"):
        raise HTTPException(400, "kind must be 'item', 'stack', or 'node'")
    if not id:
        raise HTTPException(400, "id query param required")

    out: dict[str, Any] = {"kind": kind, "id": id}
    diagnostics: list[str] = []
    since_hours_clamped = max(1, min(168, int(since_hours or 1)))

    if kind == "item":
        items = _cache.get("items") or []

        # Prefix matching is intentional for `svc:<12hex>` / `ctn:<12hex>`
        # short forms the SPA passes in. But a short bare id (e.g. `s`)
        # would prefix-match the FIRST item whose raw_id starts with
        # `s` — operator clicks "Debug" on item A and the panel renders
        # item Z's data. Gate the prefix branch on (a) the `svc:` /
        # `ctn:` prefix (legitimate short form), OR (b) minimum length
        # ≥ 12 chars (long enough to be a real id hash). Bare-id
        # equality at line 12266-67 still works for the full hash.
        def _id_matches(it: dict) -> bool:
            if it.get("id") == id or it.get("raw_id") == id:
                return True
            rid = it.get("raw_id") or ""
            if not rid or not id:
                return False
            looks_short_form = id.startswith(("svc:", "ctn:"))
            long_enough = len(id) >= 12
            return (looks_short_form or long_enough) and rid.startswith(id)

        record = next((it for it in items if _id_matches(it)), None)
        if record is None:
            raise HTTPException(404, f"no item with id={id!r}")
        out["record"] = record

        # Stack rollup if this item is part of one
        stack_name = (record.get("stack") or "").strip()
        if stack_name:
            for st in (_cache.get("stacks") or []):
                if st.get("name") == stack_name:
                    out["stack"] = st
                    break

        # Live stats entry (cpu_percent / mem_usage / has_stats / has_size)
        live_stats = (_stats_cache.get("stats") or {}).get(record.get("id")) or {}
        out["live_stats"] = live_stats

        # Historical sample density — drives the "why is my chart empty"
        # diagnostic. stats_samples is item-keyed so we can answer per-row.
        sw = _item_samples_in_window(record.get("id"), since_hours_clamped)
        out["samples_in_window"] = sw

        if not live_stats:
            diagnostics.append(
                "live_stats is empty — _stats_cache has no entry for "
                "this item. Either the sampler has not ticked since the "
                "item appeared, OR the Portainer /stats call failed for "
                "this container (check the agent on the item's node)."
            )
        else:
            if live_stats.get("has_stats") is False:
                diagnostics.append(
                    "live_stats.has_stats=false — Portainer "
                    "/containers/{id}/stats returned no usable data. "
                    "Bar will show '—'; sparkline will be empty until "
                    "stats start returning."
                )
            if live_stats.get("has_size") is False:
                diagnostics.append(
                    "live_stats.has_size=false — Portainer ?size=1 "
                    "enrichment failed. Disk bar + sparkline are empty "
                    "because there's no size_root to plot."
                )

        if sw.get("count", 0) == 0:
            diagnostics.append(
                f"stats_samples has 0 rows for this item in the past "
                f"{since_hours_clamped}h. Sampler has not persisted any "
                f"history yet — drawer charts show 'Collecting data' "
                f"until at least 2 samples land (sampler interval ~"
                f"{sw.get('expected_interval_s', 300)}s)."
            )
        elif sw.get("count", 0) < 2:
            diagnostics.append(
                f"Only {sw['count']} sample in the past "
                f"{since_hours_clamped}h. Drawer charts need ≥2 points "
                f"to render a line — wait one more sampler tick "
                f"(~{sw.get('expected_interval_s', 300)}s)."
            )
        else:
            age = int(sw.get("newest_age_s") or 0)
            expected = int(sw.get("expected_interval_s") or 300)
            if age > expected * 3:
                diagnostics.append(
                    f"Newest stats_samples row is {age}s old "
                    f"(>3× expected interval {expected}s) — sampler "
                    f"may have stalled. Charts continue rendering the "
                    f"last-known data but will look 'frozen'."
                )
            gap = int(sw.get("median_gap_s") or 0)
            if gap > expected * 1.5:
                diagnostics.append(
                    f"Median gap between consecutive samples is {gap}s "
                    f"— above 1.5× the configured {expected}s interval. "
                    f"Sampler is ticking slower than expected; check "
                    f"Admin → Logs for [stats] warnings."
                )

        out["diagnostics"] = diagnostics
        return out

    if kind == "stack":
        # First-class stack rollup lookup — no items-cache prefix
        # quirk. `_cache["stacks"]` is the per-gather rolled-up list
        # of `{name, stack_id, items[], total, updates, errors, ...}`.
        # Match on both `name` and `stack_id` so the SPA can pass
        # either identifier.
        stacks = _cache.get("stacks") or []
        record = next(
            (st for st in stacks
             if st.get("name") == id
             or str(st.get("stack_id") or "") == str(id)),
            None,
        )
        if record is None:
            raise HTTPException(404, f"no stack with id={id!r}")
        out["record"] = record

        # Per-item samples-in-window aggregate. The stack rollup
        # carries an `items` array of full item records — sum the
        # sample counts so the operator can see "stack-wide, X
        # samples landed in the past hour" without drilling into
        # every member.
        item_count = len(record.get("items") or [])
        total_samples = 0
        items_with_samples = 0
        oldest_ts = None
        newest_ts = None
        for it in (record.get("items") or []):
            iid = it.get("id")
            if not iid:
                continue
            sw = _item_samples_in_window(iid, since_hours_clamped)
            n = int(sw.get("count") or 0)
            total_samples += n
            if n > 0:
                items_with_samples += 1
                if oldest_ts is None or (sw.get("oldest_ts") and sw["oldest_ts"] < oldest_ts):
                    oldest_ts = sw.get("oldest_ts")
                if newest_ts is None or (sw.get("newest_ts") and sw["newest_ts"] > newest_ts):
                    newest_ts = sw.get("newest_ts")
        out["samples_in_window"] = {
            "hours": since_hours_clamped,
            "total_samples_across_items": total_samples,
            "items_with_samples": items_with_samples,
            "items_total": item_count,
            "oldest_ts": oldest_ts,
            "newest_ts": newest_ts,
        }

        # Aggregate by-status / by-health for one-line stack health
        # summary — same shape the by_status / by_health blocks on
        # `kind=node` produce so the SPA can render either with one
        # template path.
        by_status: dict[str, int] = {}
        by_health: dict[str, int] = {}
        for it in (record.get("items") or []):
            s = str(it.get("status") or "unknown")
            by_status[s] = by_status.get(s, 0) + 1
            h = str(it.get("health") or "unknown")
            by_health[h] = by_health.get(h, 0) + 1
        out["aggregate"] = {
            "by_status": by_status,
            "by_health": by_health,
        }

        if item_count == 0:
            diagnostics.append(
                "Stack has no items in `_cache[\"stacks\"][i].items` "
                "— either every member was filtered out (ignored / "
                "wrong stack tag) OR the gather hasn't run since the "
                "stack was deployed."
            )
        elif items_with_samples == 0:
            diagnostics.append(
                f"None of the {item_count} items in this stack have "
                f"any stats_samples rows in the past "
                f"{since_hours_clamped}h. Either every container is "
                f"newly-started (sampler hasn't ticked yet) OR the "
                f"Portainer /stats call is failing across the stack "
                f"(agent unreachable on the items' nodes)."
            )
        elif items_with_samples < item_count:
            diagnostics.append(
                f"{items_with_samples} of {item_count} items have "
                f"samples; the other "
                f"{item_count - items_with_samples} are silent — "
                f"check the per-item drawer for which ones are "
                f"missing data."
            )

        out["diagnostics"] = diagnostics
        return out

    # ----- kind == "node" --------------------------------------------
    nodes_map = _cache.get("nodes") or {}
    # _cache["nodes"] is {NodeID: hostname}; accept either as the id.
    node_id = None
    hostname = None
    for nid, hn in nodes_map.items():
        if nid == id or hn == id:
            node_id = nid
            hostname = hn
            break
    if hostname is None:
        raise HTTPException(404, f"no node with id={id!r}")
    out["node_id"] = node_id
    out["hostname"] = hostname

    items = _cache.get("items") or []
    items_on_node = [it for it in items if it.get("node") == hostname]
    by_status: dict[str, int] = {}
    by_health: dict[str, int] = {}
    for it in items_on_node:
        s = str(it.get("status") or "unknown")
        by_status[s] = by_status.get(s, 0) + 1
        h = str(it.get("health") or "unknown")
        by_health[h] = by_health.get(h, 0) + 1
    out["items_on_node"] = {
        "count": len(items_on_node),
        "by_status": by_status,
        "by_health": by_health,
        "names": [it.get("name") for it in items_on_node[:50]],
    }

    # Merged host-stats blob (same dict the host drawer renders from)
    nodes_info = (_cache.get("nodes_info") or {}).get(hostname) or {}
    out["nodes_info"] = nodes_info

    if not items_on_node:
        diagnostics.append(
            f"No items mapped to hostname={hostname!r}. Portainer's "
            f"task list returned nothing for this node — Swarm may "
            f"have evicted it OR the Portainer agent there is "
            f"unreachable."
        )
    if not nodes_info:
        diagnostics.append(
            f"nodes_info has no entry for hostname={hostname!r}. No "
            f"host-stats provider (Beszel / Pulse / node-exporter / "
            f"Webmin / SNMP / Ping) reported data for this node, so "
            f"host CPU / Memory / Disk bars stay empty."
        )

    out["diagnostics"] = diagnostics
    return out


# noinspection PyShadowingBuiltins,PyTypeChecker,PyUnresolvedReferences
@app.get("/api/hosts/debug")
async def api_hosts_debug(
    id: str = "",
    since_hours: int = 1,
    *,
    _u: AdminUser,
):
    """Admin-only diagnostic: raw provider responses + normalized
    per-provider + merged + rendered for ONE curated host.

    Purpose: spot-check what each provider is actually emitting vs
    what OmniGrid keeps after the best-of merge vs what the UI
    ultimately sees. The four sections line up so dropped fields,
    shape mismatches, or coverage gaps are visible side-by-side.

    Heavyweight by design — runs fresh probes against each enabled
    provider. Intended for interactive debugging, not polled. The UI
    fetches it lazily when the "Debug" panel in the host drawer is
    opened.
    """
    if not id:
        raise HTTPException(400, "id query param required")

    from logic import beszel as _beszel
    from logic import pulse as _pulse
    from logic import node_exporter as _ne
    from logic import host_net_sampler as _host_net_sampler
    from logic import host_metrics_sampler as _host_metrics_sampler

    curated = _load_hosts_config()
    record = next((h for h in curated if h["id"] == id), None)
    if record is None:
        raise HTTPException(404, f"no curated host with id={id!r}")

    # Which providers are live? Same derivation as api_hosts.
    active = active_host_stats_providers()

    providers_raw: dict[str, Any] = {
        "pulse": None, "beszel": None, "node_exporter": None,
        "webmin": None, "snmp": None,
    }
    providers_normalized: dict[str, Any] = {
        "pulse": None, "beszel": None, "node_exporter": None,
        "webmin": None, "snmp": None,
    }

    # ---- SNMP kickoff (early launch) -----------------------------
    # SNMP is the slowest provider in the handler — at default
    # per-host walk concurrency=1, ~67 OID branches across base +
    # Dell + Cisco + APC + UCD + Synology + Printer MIBs serialise
    # to 30-50s on slow BMC-class agents. Pre-launching it as an
    # asyncio Task here means it runs concurrently with every
    # downstream provider's `await client.get(...)` (each httpx
    # call yields to the event loop, so the SNMP probe gets to
    # advance during their wait_for / sleep / read points). Total
    # wall-clock for the handler becomes roughly max(SNMP_budget,
    # other_providers_sum) instead of SNMP + others, fitting under
    # the upstream proxy_read_timeout (~60s default on Nginx Proxy
    # Manager) even when the iDRAC pushes against the SNMP budget.
    # Result is awaited at the bottom of the handler in the SNMP
    # block where the response shape is built.
    snmp_task = None
    snmp_meta: dict[str, Any] = {}
    if "snmp" in active:
        from logic import snmp as _snmp
        if not _snmp.has_snmp_support():
            providers_raw["snmp"] = {"_error": "pysnmp not installed"}
        else:
            _raw_row_snmp_kick = record.get("snmp")
            row_snmp_kick: dict = _raw_row_snmp_kick if isinstance(_raw_row_snmp_kick, dict) else {}
            try:
                sn_aliases_kick = json.loads(
                    get_setting(Settings.SNMP_ALIASES, "{}") or "{}"
                )
                if not isinstance(sn_aliases_kick, dict):
                    sn_aliases_kick = {}
            except ValueError:
                sn_aliases_kick = {}
            # Same HARD-GATE as `_merge_one_host` — alias OR snmp_name
            # resolves the target (no bare-id fallthrough), AND
            # `record.snmp.enabled === true` is required. Hosts without
            # SNMP enrolled leave snmp_task as None, the panel hides
            # the slot.
            # Canonical SNMP resolver chain — matches the live sampler /
            # `_merge_one_host` / `api_hosts_test` paths:
            # `aliases → snmp_name → address → SKIP`. Pre-fix this
            # debug-side kickoff stopped at `snmp_name` and never
            # consulted the curated `address` field, so address-only
            # SNMP hosts had no providers_raw.snmp output in /api/hosts/debug
            # even though the live sampler probed them correctly.
            target_kick = (
                sn_aliases_kick.get(record["id"])
                or (record.get("snmp_name") or "").strip()
                or (record.get("address") or "").strip()
                or ""
            )
            enabled_kick = row_snmp_kick.get("enabled") is True
            if target_kick and enabled_kick:
                community_kick = ((row_snmp_kick.get("community") or "").strip()
                                  or (get_setting(Settings.SNMP_DEFAULT_COMMUNITY) or "public"))
                version_kick = (((row_snmp_kick.get("version") or "").strip().lower())
                                or (get_setting(Settings.SNMP_DEFAULT_VERSION) or "v2c").lower()
                                or "v2c")
                try:
                    port_kick = int(row_snmp_kick.get("port")
                                    or tuning.tuning_int(Tunable.SNMP_DEFAULT_PORT))
                except (TypeError, ValueError):
                    port_kick = 161
                v3_user_kick = ((row_snmp_kick.get("v3_user") or "").strip()
                                or get_setting(Settings.SNMP_V3_USER) or "")
                v3_auth_kick = ((row_snmp_kick.get("v3_auth_key") or "").strip()
                                or get_setting(Settings.SNMP_V3_AUTH_KEY) or "")
                v3_priv_kick = ((row_snmp_kick.get("v3_priv_key") or "").strip()
                                or get_setting(Settings.SNMP_V3_PRIV_KEY) or "")
                # Per-host walk_concurrency override — Dell iDRAC9 /
                # iDRAC10 and other server-class BMCs handle parallel
                # queries fine and benefit dramatically from
                # concurrency > 1. The safety-floor concurrency=1
                # default is for low-power embedded snmpd's that drop
                # UDP packets at higher concurrency.
                walk_conc_kick = row_snmp_kick.get("walk_concurrency")
                try:
                    walk_conc_kick = int(walk_conc_kick) if walk_conc_kick else None
                except (TypeError, ValueError):
                    walk_conc_kick = None
                # Per-host vendor MIB selector. None = auto-detect from
                # sysDescr; explicit list = bypass auto-detect.
                vendors_kick = _clean_vendors_input(row_snmp_kick.get("vendors"))
                # Per-host wall_clock_budget override capped at
                # the debug-path ceiling. The DEBUG-PATH budget is
                # deliberately tighter than the sampler-path budget
                # because the debug panel traverses
                # browser → NPM → OmniGrid (NPM's `proxy_read_timeout`
                # default is 60s; raising the global SNMP budget above
                # that surfaces as HTTP 504 from NPM, NOT a useful
                # error). The internal sampler path runs lifespan-side,
                # never touches NPM, so its budget is uncapped via the
                # global tunable. Operators with a 120s+ global
                # tunable have set it for the sampler — the debug
                # panel ceiling stays at 50s so the proxied request
                # always completes within the NPM window. Per-host
                # override can DECREASE the budget below 50s but not
                # raise it above. The operator's recovery for slow
                # iDRAC chassis is to bump the per-host
                # `snmp.walk_concurrency` (the probe finishes faster),
                # NOT to raise the budget — the error message already
                # prompts that path.
                wcb_kick = row_snmp_kick.get("wall_clock_budget")
                try:
                    wcb_kick_f = float(wcb_kick) if wcb_kick else None
                except (TypeError, ValueError):
                    wcb_kick_f = None
                _DEBUG_BUDGET_CAP = 50.0
                wcb_resolved = (
                    min(_DEBUG_BUDGET_CAP, wcb_kick_f)
                    if wcb_kick_f else _DEBUG_BUDGET_CAP
                )
                snmp_task = asyncio.create_task(_snmp.probe_snmp(
                    target_kick,
                    community=community_kick,
                    version=version_kick,
                    port=port_kick,
                    v3_user=v3_user_kick,
                    v3_auth_key=v3_auth_kick,
                    v3_priv_key=v3_priv_kick,
                    walk_concurrency=walk_conc_kick,
                    vendors=vendors_kick,
                    timeout=8.0,
                    active_sources=active,
                    verbose=True,
                    bypass_cooldown=True,
                    wall_clock_budget=wcb_resolved,
                ))
                snmp_meta = {
                    "target": target_kick,
                    "community": community_kick,
                    "version": version_kick,
                    "port": port_kick,
                    "v3_user": v3_user_kick,
                    "v3_auth_set": bool(v3_auth_kick),
                    "v3_priv_set": bool(v3_priv_kick),
                    # Per-host override + global tunable so the operator
                    # can see WHICH value the probe used. None = "no
                    # per-host override, fell back to the global
                    # tunable" — the resolved field shows the actual
                    # value used inside probe_snmp.
                    "walk_concurrency": walk_conc_kick,
                    "walk_concurrency_global": int(
                        tuning.tuning_int(Tunable.SNMP_PER_HOST_WALK_CONCURRENCY)
                    ),
                }

    # ---- Beszel --------------------------------------------------
    if "beszel" in active and record.get("beszel_name"):
        hub_url = get_setting(Settings.BESZEL_HUB_URL) or ""
        ident = get_setting(Settings.BESZEL_IDENTITY) or ""
        passw = get_setting(Settings.BESZEL_PASSWORD) or ""
        verify = (get_setting(Settings.BESZEL_VERIFY_TLS, "true") or "true").lower() == "true"
        if hub_url and ident and passw:
            try:
                async with httpx.AsyncClient(verify=verify, timeout=8.0) as client:
                    token = await _beszel.get_token(client, hub_url, ident, passw)
                    try:
                        records = await _beszel.fetch_systems(client, hub_url, token)
                    except PermissionError:
                        token = await _beszel.get_token(
                            client, hub_url, ident, passw, force_refresh=True,
                        )
                        records = await _beszel.fetch_systems(client, hub_url, token)
                    latest_stats: dict = {}
                    try:
                        latest_stats = await _beszel.fetch_latest_stats(
                            client, hub_url, token,
                        )
                    except Exception as e:
                        latest_stats = {"_fetch_error": str(e)}
                target = (record["beszel_name"] or "").strip()
                match = None
                for rec in records:
                    info = rec.get("info") or {}
                    host_key = (
                        (rec.get("host") or "").strip()
                        or (info.get("h") or "").strip()
                        or (rec.get("name") or "").strip()
                    )
                    if host_key == target:
                        match = rec
                        break
                if match:
                    rec_id = match.get("id") or ""
                    stats_row = latest_stats.get(rec_id) if isinstance(latest_stats, dict) else None
                    providers_raw["beszel"] = {
                        "match_key": target,
                        "record": match,
                        "stats_row": stats_row,
                    }
                    providers_normalized["beszel"] = _beszel.extract_stats(
                        match.get("info") or {}, stats_row,
                    )
                else:
                    known = sorted((
                        (r.get("host") or (r.get("info") or {}).get("h") or r.get("name") or "")
                        for r in records
                    ), key=str.lower)
                    providers_raw["beszel"] = {
                        "_error": f"no record matched beszel_name={target!r}",
                        "known_host_keys": known[:25],
                    }
            except Exception as e:
                providers_raw["beszel"] = {"_error": str(e)}
        else:
            providers_raw["beszel"] = {"_error": "Beszel creds not configured"}

    # ---- Pulse ---------------------------------------------------
    if "pulse" in active and record.get("pulse_name"):
        pulse_url = get_setting(Settings.PULSE_URL) or ""
        pulse_tok = get_setting(Settings.PULSE_TOKEN) or ""
        verify = (get_setting(Settings.PULSE_VERIFY_TLS, "true") or "true").lower() == "true"
        if pulse_url and pulse_tok:
            try:
                async with httpx.AsyncClient(verify=verify, timeout=8.0) as client:
                    state = await _pulse.fetch_state(client, pulse_url, pulse_tok)
                probe = await _pulse.probe_pulse(
                    pulse_url, pulse_tok, verify_tls=verify,
                )
                normalized_match = _pulse.lookup(
                    probe.get("hosts") or {}, record["pulse_name"],
                )
                target_lc = (record["pulse_name"] or "").strip().lower()
                # Node-shaped match first (exact hostname). Then fall
                # through to any guest whose name / vmid matches.
                raw_match = None
                for n in (state.get("nodes") or []):
                    if not isinstance(n, dict):
                        continue
                    name = (n.get("node") or n.get("name") or "").strip().lower()
                    if name == target_lc:
                        raw_match = {"kind": "node", "data": n}
                        break
                if raw_match is None:
                    # Shallow walk of common guest containers — enough
                    # for a debug dump without reproducing probe_pulse's
                    # full recursive harvest.
                    candidates: list = []
                    for key in ("vms", "containers", "guests", "lxc", "qemu"):
                        v = state.get(key)
                        if isinstance(v, list):
                            candidates.extend(v)
                    _raw_pve = state.get("pve")
                    pve: dict = _raw_pve if isinstance(_raw_pve, dict) else {}
                    for key in ("vms", "containers", "guests", "lxc", "qemu"):
                        v = pve.get(key) if isinstance(pve, dict) else None
                        if isinstance(v, list):
                            candidates.extend(v)
                    for g in candidates:
                        if not isinstance(g, dict):
                            continue
                        name = (g.get("name") or g.get("hostname") or g.get("id") or "").strip().lower()
                        vmid = str(g.get("vmid") or "").strip().lower()
                        if name == target_lc or vmid == target_lc:
                            raw_match = {"kind": g.get("type") or "guest", "data": g}
                            break
                providers_raw["pulse"] = {
                    "match_key": record["pulse_name"],
                    "state_top_keys": sorted(state.keys()) if isinstance(state, dict) else [],
                    "nodes_count": len(state.get("nodes") or []),
                    "matched_raw": raw_match,
                }
                providers_normalized["pulse"] = normalized_match
            except Exception as e:
                providers_raw["pulse"] = {"_error": str(e)}
        else:
            providers_raw["pulse"] = {"_error": "Pulse creds not configured"}

    # ---- node-exporter -------------------------------------------
    if "node_exporter" in active and record.get("ne_url"):
        url_input = record["ne_url"]
        # Normalise the operator-supplied URL the same way probe_node()
        # does so the "Raw" debug dump shows real metric text, not the
        # HTML landing page that bare host:port returns.
        url_canonical = _ne.normalise_ne_url(url_input)
        # operator-tunable NE probe timeout.
        _ne_timeout = tuning.tuning_int(Tunable.NODE_EXPORTER_PROBE_TIMEOUT_SECONDS)
        try:
            async with httpx.AsyncClient(verify=False, timeout=float(_ne_timeout)) as client:
                r = await client.get(url_canonical)
                r.raise_for_status()
                text = r.text
                stats = await _ne.probe_node(client, url_input)
            lines = text.splitlines()
            # Cap the sample — a loaded node-exporter can emit thousands
            # of metric lines; operators want a taste, not a dump.
            providers_raw["node_exporter"] = {
                "url_input": url_input,
                "url_canonical": url_canonical,
                "size_bytes": len(text),
                "line_count": len(lines),
                "sample_lines": lines[:80],
                # Last 5 host_net_samples rows for this host. Lets an
                # operator confirm the NE-net fallback sampler is
                # filling the series at the expected cadence; if this
                # is empty but the exporter returns non-zero rx/tx
                # totals, the sampler hasn't run yet (first 5-min tick)
                # or every delta has been rejected by sanity bounds.
                "recent_net_samples": _host_net_sampler.last_samples(record["id"]),
                # Last 5 host_metrics_samples rows for this host. The
                # sampler writes one row per STATS_SAMPLE_INTERVAL
                # (default 5 min) when NE returns meaningful gauges or
                # sane-bounded counter deltas; see
                # logic.host_metrics_sampler._compute_row.
                "recent_metrics_samples": _host_metrics_sampler.last_samples(record["id"]),
            }
            providers_normalized["node_exporter"] = stats
        except Exception as e:
            providers_raw["node_exporter"] = {"_error": str(e)}

    # ---- Webmin --------------------------------------------------
    if "webmin" in active:
        try:
            wm_aliases = json.loads(get_setting(Settings.WEBMIN_ALIASES, "{}") or "{}")
            if not isinstance(wm_aliases, dict):
                wm_aliases = {}
        except ValueError:
            wm_aliases = {}
        wm_url = (wm_aliases.get(record["id"]) or "").strip().rstrip("/")
        user = get_setting(Settings.WEBMIN_USER) or ""
        passw = get_setting(Settings.WEBMIN_PASSWORD) or ""
        verify = (get_setting(Settings.WEBMIN_VERIFY_TLS, "false") or "false").lower() == "true"
        if not wm_url:
            # No Webmin URL mapped for this host — that's an
            # intentional "this host doesn't use Webmin" state, not an
            # error. Leave providers_raw["webmin"] as None so the
            # debug panel's hasDebugData() wrapper hides the block
            # entirely instead of surfacing a misleading error chip.
            pass
        elif not (user and passw):
            providers_raw["webmin"] = {"_error": "Webmin creds not configured"}
        else:
            from logic import webmin as _webmin
            try:
                r = await _webmin.probe_webmin(
                    wm_url, user, passw, verify_tls=verify, timeout=8.0,
                    active_sources=active,
                )
                providers_raw["webmin"] = {
                    "url": wm_url,
                    "hosts_keys": sorted((r.get("hosts") or {}).keys()),
                    "partial_errors": r.get("partial_errors") or [],
                    "error": r.get("error"),
                }
                if r.get("hosts"):
                    providers_normalized["webmin"] = next(iter(r["hosts"].values()))
            except Exception as e:
                providers_raw["webmin"] = {"_error": str(e)}

    # ---- Ping — most recent samples + the resolved sampler
    #    target so the operator can see exactly what address the
    #    probe is hitting (DNS failure debugging). Only renders
    #    when ping is in active AND this host is opted in. -------
    if "ping" in active and bool((record.get("ping") or {}).get("enabled", False)):
        try:
            from logic import ping_sampler as _ping_sampler_dbg
            from logic import ping as _ping_dbg
            samples = _ping_sampler_dbg.last_samples(record["id"]) or []
            # Replicate the sampler's target-resolution chain so the
            # debug surface shows the same `host` the probe is using.
            ping_cfg = (record.get("ping") or {}) if isinstance(record.get("ping"), dict) else {}
            ssh_cfg = (record.get("ssh") or {}) if isinstance(record.get("ssh"), dict) else {}
            target = (
                (record.get("address") or "").strip()
                or (ping_cfg.get("host") or "").strip()
                or (ssh_cfg.get("fqdn") or "").strip()
                or (ssh_cfg.get("host") or "").strip()
                or record["id"]
            )
            providers_raw["ping"] = {
                "target": target,
                "port": ping_cfg.get("port"),
                "transport": ping_cfg.get("transport") or "(global default)",
                "icmp_supported": _ping_dbg.has_icmp_support(),
                "samples_count": len(samples),
                "last_samples": samples,
            }
            if samples:
                last = samples[0]
                stats = _ping_dbg.to_host_stats({
                    "alive": last.get("alive"),
                    "rtt_ms": last.get("rtt_ms"),
                    "loss_pct": last.get("loss_pct"),
                })
                if stats:
                    providers_normalized["ping"] = stats
        except Exception as e:
            providers_raw["ping"] = {"_error": str(e)}

    # ---- SNMP (await the early-launched probe) -------------------
    # The probe was kicked off at the top of the handler (see "SNMP
    # kickoff (early launch)" block above) so it could run
    # concurrently with the Beszel / Pulse / NE / Webmin / Ping
    # awaits. Now we synchronise on the result and build the response
    # shape. Hosts without SNMP enrolled have snmp_task = None and
    # providers_raw["snmp"] was already set above — we just skip.
    if snmp_task is not None:
        try:
            r = await snmp_task
            providers_raw["snmp"] = {
                "target": snmp_meta["target"],
                "community": snmp_meta["community"],
                "version": snmp_meta["version"],
                "port": snmp_meta["port"],
                "v3_user": snmp_meta["v3_user"],
                "v3_auth_set": snmp_meta["v3_auth_set"],
                "v3_priv_set": snmp_meta["v3_priv_set"],
                "hosts_keys": sorted((r.get("hosts") or {}).keys()),
                "error": r.get("error"),
                # Full probed data: every parsed OID, per-row
                # storage table (RAM + disks), per-row interface
                # counters, plus a walk-summary header so operators
                # can see at a glance which OID families the agent
                # answered.
                "raw": r.get("raw") or {},
            }
            if r.get("hosts"):
                providers_normalized["snmp"] = next(iter(r["hosts"].values()))
        except Exception as e:  # noqa: BLE001
            providers_raw["snmp"] = {"_error": str(e)}

    # ---- Merged (best-of) ----------------------------------------
    merged: dict = {}
    # Order matches the runtime merge order in `_merge_one_host` /
    # `gather.py`: Pulse → SNMP → Beszel → node-exporter → Webmin.
    # Keeps the debug panel's "merged" view byte-identical to what the
    # SPA shows on the live row.
    for src in ("pulse", "snmp", "beszel", "node_exporter", "webmin"):
        stats = providers_normalized.get(src)
        if stats:
            _merge_best(merged, stats)

    # ---- Rendered — what `_shape_host_api_row` would emit for this
    # host given the merged dict we just built. Pre-fix this called
    # `api_hosts()` (full fleet re-probe, then `next(... if h.id == id)`)
    # which fired EVERY provider against EVERY curated host on every
    # debug request — a 200-host fleet then re-probed every neighbour
    # before returning, easily blowing past NPM's 60s proxy_read_timeout.
    # The shape helper is purely a synchronous projection of merged +
    # per-host providers_hit, so we can derive `rendered` without any
    # extra network probe.
    try:
        providers_hit = sorted(
            p for p, raw in providers_raw.items()
            if raw is not None and not (
                isinstance(raw, dict) and "_error" in raw and len(raw) == 1
            )
        )
        rendered = _shape_host_api_row(
            record, merged, providers_hit,
            any_provider_enabled=bool(active),
            active=active,
        )
    except Exception as e:
        rendered = {"_error": str(e)}

    # Per-host active providers — global `active` list intersected
    # with what's actually mapped on THIS host's curated config.
    # Without this, the debug panel's "Active providers" row showed
    # the operator the GLOBAL set even on a row that only had ping
    # enabled — misleading, because the other providers wouldn't
    # actually probe this host. Operator-reported on the ftth row
    # (ping-only) showing "beszel, node_exporter, ping, pulse".
    host_active = sorted(
        p for p in active
        if (p == "beszel" and (record.get("beszel_name") or "").strip())
        or (p == "pulse" and (record.get("pulse_name") or "").strip())
        or (p == "node_exporter" and (record.get("ne_url") or "").strip())
        or (p == "webmin" and (record.get("webmin_name") or "").strip())
        or (p == "ping" and bool((record.get("ping") or {}).get("enabled", False)))
        # SNMP is "active for this host" only when (a) the operator has
        # mapped a probe target (alias OR per-row `snmp_name` OR the
        # shared `address` field) AND (b) the per-row `snmp.enabled
        # === True` opt-in flag is set. The probe-side gate in
        # `_merge_one_host` uses the canonical `aliases → snmp_name →
        # address → SKIP` chain — this gate must accept the same
        # alternatives or the debug panel will hide SNMP rows for
        # address-only hosts even when the live sampler probes them
        # successfully.
        or (p == "snmp" and bool(
            isinstance(record.get("snmp"), dict)
            and record["snmp"].get("enabled") is True
            and (
                (record.get("snmp_name") or "").strip()
                or (record.get("address") or "").strip()
            )
        ))
    )
    # Per-host counters — operator-requested addition. Surfaces
    # failure-state retry counters, per-provider pause / last-ok rows,
    # and time-series row counts so operators can debug "why is my host
    # paused" / "why is my chart empty" without poking the SQLite DB
    # directly.
    counters: dict = {}
    try:
        counters["failure_state"] = _failure_state_for_host(id)
    except Exception as e:
        counters["failure_state"] = {"_error": str(e)}
    try:
        counters["provider_pause_state"] = _provider_pause_state_for_host(id)
    except Exception as e:
        counters["provider_pause_state"] = {"_error": str(e)}
    try:
        with db_conn() as c:
            # host_snmp_samples — SNMP probe history depth.
            row = c.execute(
                "SELECT COUNT(*), MAX(ts), MIN(ts) "
                "FROM host_snmp_samples WHERE host_id=?",
                (id,),
            ).fetchone()
            counters["snmp_samples"] = {
                "count": int(row[0] or 0),
                "newest_ts": (int(row[1]) if row[1] is not None else None),
                "oldest_ts": (int(row[2]) if row[2] is not None else None),
            }
            # host_snmp_iface_samples — per-port history depth.
            row2 = c.execute(
                "SELECT COUNT(*), COUNT(DISTINCT ifname), MAX(ts) "
                "FROM host_snmp_iface_samples WHERE host_id=?",
                (id,),
            ).fetchone()
            counters["snmp_iface_samples"] = {
                "rows": int(row2[0] or 0),
                "ifaces": int(row2[1] or 0),
                "newest_ts": (int(row2[2]) if row2[2] is not None else None),
            }
            # host_metrics_samples — node-exporter sampler history.
            row3 = c.execute(
                "SELECT COUNT(*), MAX(ts) "
                "FROM host_metrics_samples WHERE host_id=?",
                (id,),
            ).fetchone()
            counters["ne_samples"] = {
                "count": int(row3[0] or 0),
                "newest_ts": (int(row3[1]) if row3[1] is not None else None),
            }
            # ping_samples — TCP/ICMP probe history.
            row4 = c.execute(
                "SELECT COUNT(*), MAX(ts), "
                "       SUM(CASE WHEN alive=1 THEN 1 ELSE 0 END), "
                "       SUM(CASE WHEN alive=0 THEN 1 ELSE 0 END) "
                "FROM ping_samples WHERE host_id=?",
                (id,),
            ).fetchone()
            counters["ping_samples"] = {
                "count": int(row4[0] or 0),
                "newest_ts": (int(row4[1]) if row4[1] is not None else None),
                "alive": int(row4[2] or 0),
                "down": int(row4[3] or 0),
            }
            # host_snapshots — last persistence write for this host.
            row5 = c.execute(
                "SELECT ts, length(data) FROM host_snapshots WHERE host=?",
                (id,),
            ).fetchone()
            if row5:
                counters["snapshot"] = {
                    "ts": float(row5[0] or 0.0),
                    "size_bytes": int(row5[1] or 0),
                }
            else:
                # Try short-hostname fallback (mirrors the snapshot
                # lookup tolerance in apply_host_snapshot_fallback).
                # LIKE pattern needs ESCAPE so a hostname containing
                # `_` (e.g. `web_01`) doesn't match unrelated hosts via
                # the underscore-wildcard. Same security drift class
                # as the bulk-resume + timeline sites.
                short = (id or "").split(".", 1)[0]
                row5b = c.execute(
                    "SELECT host, ts, length(data) FROM host_snapshots "
                    "WHERE host=? OR host LIKE ? ESCAPE '\\'",
                    (short, _sqlite_like_escape(short) + ".%"),
                ).fetchone()
                if row5b:
                    counters["snapshot"] = {
                        "ts": float(row5b[1] or 0.0),
                        "size_bytes": int(row5b[2] or 0),
                        "host_key": row5b[0],
                    }
                else:
                    counters["snapshot"] = None
    except Exception as e:
        counters["_db_error"] = str(e)

    # ---- Samples in window — per-time-range diagnostic. -----------
    # Operator-flagged: charts can show "cut" data in the past hour
    # (gaps in the polyline, missing buckets at the head / tail).
    # The counters above show TOTAL row counts since the host's first
    # sample — not useful when diagnosing "why is the past hour
    # missing data?". This block answers exactly that question:
    # for each time-series table, how many rows landed within the
    # `since_hours` window, what's the most-recent / oldest
    # timestamp inside it, and the median gap between consecutive
    # samples (lets the operator see whether the sampler's been
    # ticking on cadence or skipping). Window mirrors the chart
    # range picker (1 / 6 / 24 / 168 hours) so the SPA passes the
    # same value the user has selected and the count matches
    # what's plotted.
    window_hours = max(1, min(168, int(since_hours or 1)))
    since_ts = int(time.time() - window_hours * 3600)
    samples_in_window: dict = {"hours": window_hours, "since_ts": since_ts}
    try:
        with db_conn() as c:
            for table in (
                    "host_snmp_samples", "host_snmp_iface_samples",
                    "host_metrics_samples", "ping_samples",
                    "host_net_samples",
                    # Pulse / Webmin / Beszel each write to their own
                    # per-provider sample tables. Beszel was added under
                    # the "every host-stats provider must have a local
                    # sample store" rule — pre-fix it was the read-
                    # through-only outlier and chart cuts followed.
                    "host_pulse_samples", "host_webmin_samples",
                    "host_beszel_samples",
            ):
                try:
                    row = c.execute(
                        f"SELECT COUNT(*), MIN(ts), MAX(ts) "
                        f"FROM {table} WHERE host_id = ? AND ts >= ?",
                        (id, since_ts),
                    ).fetchone()
                except Exception as e:
                    samples_in_window[table] = {"_error": str(e)}
                    continue
                count = int(row[0] or 0)
                oldest = int(row[1]) if row[1] is not None else None
                newest = int(row[2]) if row[2] is not None else None
                # Median gap between consecutive samples — a flat
                # cadence sampler should produce a near-constant gap
                # (~5 min for `host_metrics_samples`, ~1 min for
                # `ping_samples`, etc.). A median gap >> the
                # configured interval flags a sampler that's been
                # skipping ticks; a median gap == the interval is
                # healthy. SQLite doesn't have a built-in median, so
                # we lift up to 200 timestamps and compute it Python-
                # side. Cap at 200 to bound the read for a 7-day
                # ping window which can carry ~10000 rows.
                gaps_median: Optional[int] = None
                if count >= 2:
                    try:
                        ts_rows = c.execute(
                            f"SELECT ts FROM {table} "
                            f"WHERE host_id = ? AND ts >= ? "
                            f"ORDER BY ts ASC LIMIT 200",
                            (id, since_ts),
                        ).fetchall()
                        ts_list = [int(r[0]) for r in ts_rows]
                        gaps = [b - a for a, b in zip(ts_list, ts_list[1:])]
                        if gaps:
                            gaps.sort()
                            mid = len(gaps) // 2
                            gaps_median = (
                                gaps[mid] if len(gaps) % 2 == 1
                                else (gaps[mid - 1] + gaps[mid]) // 2
                            )
                    except (IndexError, TypeError, ValueError):
                        gaps_median = None
                samples_in_window[table] = {
                    "count": count,
                    "newest_ts": newest,
                    "oldest_ts": oldest,
                    "median_gap_s": gaps_median,
                    "newest_age_s": (
                        int(time.time() - newest) if newest is not None
                        else None
                    ),
                }
    except Exception as e:
        samples_in_window["_db_error"] = str(e)

    counters["samples_in_window"] = samples_in_window

    # EVERY tunable, surfaced live-resolved. Pre-fix this was an
    # explicit list of ~36 keys grouped by provider — discoverable but
    # incomplete: port-scan / SSH / AI / config tunables weren't
    # included, so an operator asking the AI "what's the AI fallback
    # max depth?" or "what's the SSH WS heartbeat?" got a non-answer
    # because the value never reached the AI palette context.
    # Reading the canonical TUNABLES table verbatim makes the panel
    # exhaustive: providers (Beszel / Pulse / NE / Webmin / SNMP /
    # Ping) + port-scan + SSH + AI integration + config knobs all
    # appear automatically. Adding a new tunable requires a single
    # entry in `logic/tuning.py:TUNABLES` and it's surfaced here on
    # the next request — no list-edit drift class.
    from logic.tuning import tuning_int as _tuning_int, TUNABLES as _TUNABLES
    counters["tunables"] = {}
    for key in _TUNABLES.keys():
        try:
            counters["tunables"][key] = _tuning_int(key)
        except (ValueError, TypeError, KeyError):
            # Bounds-clamp / DB error; skip silently rather than
            # poisoning the whole tunables map for one bad knob.
            pass

    # Strip the sampler-internal `host_services_raw` blob from the
    # merged dict before serialising. It's a 50-200-row systemd unit
    # list that the lifespan `host_beszel_sampler` consumes once-per-
    # tick and persists to `host_beszel_services`; downstream
    # consumers (this debug response, `_shape_host_api_row`, the AI
    # palette context) read the rolled summary or hit the dedicated
    # /api/hosts/{id}/beszel/services endpoint instead. Leaving the
    # raw list in the merged dict bloats the debug response by
    # several KB on hosts with many tracked units AND risks accidental
    # leak via any future code path that ships merged verbatim.
    merged_for_debug = (
        {k: v for k, v in merged.items() if k != "host_services_raw"}
        if isinstance(merged, dict) else merged
    )
    return {
        "host_record": record,
        "active_providers": host_active,
        "active_providers_global": sorted(active),
        "providers_raw": providers_raw,
        "providers_normalized": providers_normalized,
        "merged": merged_for_debug,
        "rendered": rendered,
        "counters": counters,
    }


# ============================================================================
# SSH console — admin-only remote-command runner for the host drawer.
#
# Surface:
# GET  /api/hosts/{host_id}/ssh/status  — resolved connection params
# POST /api/hosts/{host_id}/ssh/test    — runs `whoami` with a short timeout
# POST /api/hosts/{host_id}/ssh/run     — body {command, dry_run}
#
# Every runner call lands in the history table as op_type='ssh_run' so
# Admin → History carries a complete audit trail. Destructive-command
# typed-confirm (hostname echo) is enforced on the UI — the backend
# merely returns a ``destructive`` flag + matched patterns so the UI
# knows to raise the bar. Backend still always runs dry-run safely.
# ============================================================================
def _ssh_write_audit_row(
    *,
    op_id: str,
    actor: str,
    host_id: str,
    command: str,
    result: dict,
) -> None:
    """Persist one SSH run into the ``history`` table.

    Uses ``op_type='ssh_run'`` so the History view (which filters by
    op_type) naturally surfaces the audit trail alongside updates /
    restarts. The command is sanitised via
    :func:`logic.ssh.sanitize_command_for_audit` before landing — not a
    security boundary (sshd on the target still sees the raw line) but
    keeps long one-liners readable in the UI and masks obvious secret
    flags so a History export isn't a liability on its own.

    Mirrors the direct-insert pattern used by the scheduler's
    gather_refresh / backup runners (see ``logic/schedules.py``) — we
    don't route through ops.persist_history because that bumps a
    Prometheus counter whose label set is keyed to the fixed op_type
    enum. Keep ssh_run out of that counter until we decide the
    dashboards want it.
    """
    from logic import ssh as _ssh
    started = time.time()
    status = "success" if result.get("ok") and not result.get("error") else "error"
    if result.get("dry_run"):
        status = "dry_run"
    error = result.get("error")
    duration = (result.get("duration_ms") or 0) / 1000.0
    events = [
        {
            "ts": time.time(),
            "level": "info" if status in ("success", "dry_run") else "error",
            "msg": (
                f"ssh_run dry_run={bool(result.get('dry_run'))} "
                f"exit={result.get('exit_code')} "
                f"stdout_bytes={len(result.get('stdout') or '')} "
                f"stderr_bytes={len(result.get('stderr') or '')}"
            ),
        }
    ]
    _ops_mod.assert_op_type("ssh_run")
    try:
        with db_conn() as c:
            c.execute(
                "INSERT INTO history "
                "(ts, op_type, target_kind, target_name, target_id, "
                " target_stack, status, duration, events, error, actor) "
                "VALUES (?, ?, 'ssh', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    started, "ssh_run",
                    _ssh.sanitize_command_for_audit(command) or "(empty)",
                    f"{host_id}:{op_id}",
                    None,
                    status, duration,
                    json.dumps(events),
                    error, actor,
                ),
            )
    except Exception as e:
        # Never let audit-log failure break the response — an operator
        # needs to see the result even if the history write blew up.
        print(f"[ssh] audit-log insert failed: {e}")


@app.get("/api/hosts/{host_id}/ssh/status")
async def api_ssh_status(
    host_id: str,
    _admin: AdminUser,
):
    """Return the resolved SSH connection params for one host.

    Does NOT initiate a TCP connection — safe to poll on drawer open.
    Surfaces ``configured`` + ``enabled`` flags the UI uses to gate
    the Run button.
    """
    from logic import ssh as _ssh
    return _ssh.ssh_status(host_id, _load_hosts_config())


@app.post("/api/hosts/{host_id}/ssh/test")
async def api_ssh_test(
    host_id: str,
    request: Request,
    _admin: AdminUser,
):
    """Admin-only: run `whoami` on the host to verify connectivity.

    Persists a history row (``op_type='ssh_run'``) so repeated failed
    tests are visible in the audit trail. Body is ignored — everything
    is keyed off the persisted settings + curated hosts_config row.
    """
    from logic import ssh as _ssh
    result = await _ssh.test_connection(host_id, _load_hosts_config())
    actor = getattr(request.state, "user", None)
    actor_name = getattr(actor, "username", None) or "unknown"
    _ssh_write_audit_row(
        op_id=uuid.uuid4().hex[:8],
        actor=actor_name,
        host_id=host_id,
        command="whoami  # ssh test",
        result=result,
    )
    return result


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.post("/api/hosts/{host_id}/ssh/run")
async def api_ssh_run(
    host_id: str,
    body: dict,
    request: Request,
    _admin: AdminUser,
):
    """Admin-only: run one command over SSH.

    Body:
        command (str, required)
        dry_run (bool, default true) — false to actually execute

    Always dry-run-safe: the frontend is expected to preflight with
    ``dry_run: true`` and surface the resolved connection before
    offering a "Run for real" button. Backend enforcement is a
    length-cap + destructive-pattern detection; typed-hostname confirm
    is a UI concern. Every call lands in the history table as
    ``op_type='ssh_run'``.
    """
    from logic import ssh as _ssh
    command = (body or {}).get("command")
    if not isinstance(command, str) or not command.strip():
        raise HTTPException(400, "command is required")
    if len(command) > _ssh.MAX_COMMAND_LEN:
        raise HTTPException(
            400,
            f"command exceeds {_ssh.MAX_COMMAND_LEN}-byte cap "
            f"({len(command)} bytes)",
        )
    dry_run = bool((body or {}).get("dry_run", True))
    timeout = (body or {}).get("timeout")
    try:
        timeout_f = float(timeout) if timeout is not None else 30.0
    except (TypeError, ValueError):
        timeout_f = 30.0
    timeout_f = max(1.0, min(timeout_f, 120.0))

    destructive_hits = _ssh.command_is_destructive(command)
    result = await _ssh.run_command(
        host_id=host_id,
        command=command,
        hosts_config=_load_hosts_config(),
        timeout=timeout_f,
        dry_run=dry_run,
    )
    result["destructive"] = destructive_hits
    actor = getattr(request.state, "user", None)
    actor_name = getattr(actor, "username", None) or "unknown"
    _ssh_write_audit_row(
        op_id=uuid.uuid4().hex[:8],
        actor=actor_name,
        host_id=host_id,
        command=command,
        result=result,
    )
    return result


# ----------------------------------------------------------------------------
# Interactive SSH terminal
# Browser <—WSS—> OmniGrid backend <—asyncssh shell—> target host.
#
# Auth: same og_session cookie as every other admin-only API path. The WS
# upgrade is rejected with code=4401 when the cookie is missing / invalid /
# the user isn't admin. Bearer-token auth is intentionally NOT supported
# here — interactive shells are operator workflows; machine clients use
# /api/hosts/{id}/ssh/run.
#
# Audit: a row is written to ``history`` at session-OPEN with status
# ``running`` and updated to ``success`` / ``failed`` at session-CLOSE.
# Keystrokes / shell I/O are NEVER logged (privacy + audit volume) — only
# the open / close events.
#
# Keep-alive: the route pings the WS every ~25s so NPM / Cloudflare idle
# timeouts don't drop a quiet shell. ``open_shell`` already passes
# ``keepalive_interval=15`` to asyncssh on the upstream side.
# ----------------------------------------------------------------------------
def _ssh_terminal_audit_open(
    *,
    host_id: str,
    actor: str,
    resolved: dict,
) -> Optional[int]:
    """Insert the session-OPEN history row. Returns the new rowid or
    ``None`` if the insert failed (audit-log breakage must never block
    the session itself — operator visibility is best-effort by design).
    """
    _ops_mod.assert_op_type("ssh_terminal")
    try:
        with db_conn() as c:
            cur = c.execute(
                "INSERT INTO history "
                "(ts, op_type, target_kind, target_name, target_id, "
                " target_stack, status, duration, events, error, actor) "
                "VALUES (?, ?, 'ssh', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    "ssh_terminal",
                    f"{resolved.get('user') or '?'}@{resolved.get('host') or host_id}",
                    f"{host_id}",
                    None,
                    "running",
                    0.0,
                    json.dumps([{
                        "ts": time.time(),
                        "level": "info",
                        "msg": (
                            f"ssh_terminal start "
                            f"target={resolved.get('user')}@{resolved.get('host')}:{resolved.get('port')}"
                        ),
                    }]),
                    None,
                    actor,
                ),
            )
            return cur.lastrowid
    except Exception as e:
        print(f"[ssh] terminal audit-open insert failed: {e}")
        return None


def _ssh_terminal_audit_close(
    *,
    row_id: Optional[int],
    started_at: float,
    status: str,
    error: Optional[str],
    bytes_in: int,
    bytes_out: int,
) -> None:
    """Update the session-OPEN row to its final state. Fire-and-forget;
    failures are logged but never raised.
    """
    if not row_id:
        return
    duration = max(0.0, time.time() - started_at)
    events = [{
        "ts": time.time(),
        "level": "info" if status == "success" else "error",
        "msg": (
            f"ssh_terminal end status={status} "
            f"bytes_in={bytes_in} bytes_out={bytes_out} "
            f"duration={duration:.1f}s"
        ),
    }]
    try:
        with db_conn() as c:
            c.execute(
                "UPDATE history SET status=?, duration=?, events=?, error=? "
                "WHERE id=?",
                (status, duration, json.dumps(events), error, row_id),
            )
    except Exception as e:
        print(f"[ssh] terminal audit-close update failed: {e}")


# Registered BEFORE the StaticFiles "/" catch-all per CLAUDE.md mount-order
# rule — the catch-all responds to every path and would shadow the
# WebSocket route otherwise.
# noinspection PyTypeChecker,PyUnresolvedReferences
@app.websocket("/api/hosts/{host_id}/ssh/terminal")
async def ws_ssh_terminal(websocket: WebSocket, host_id: str):
    """Bridge a browser WebSocket to a live PTY-backed SSH shell.

    Frame protocol (browser → backend):
      - **binary**   — raw stdin bytes (forwarded verbatim to the shell).
      - **text JSON**  — control message:
            ``{"type": "resize", "cols": N, "rows": M}``
            ``{"type": "ping"}``  (no-op; server pings are separate)

    Frame protocol (backend → browser):
      - **binary**   — raw stdout bytes from the shell.
      - **text JSON**  — control message:
            ``{"type": "ready", "resolved": {...}}``  on shell open.
            ``{"type": "error", "code": "...", "message": "..."}``  fatal.
            ``{"type": "exit",  "code": N}``  shell exited cleanly.

    Cookie auth is enforced at the upgrade — the route REJECTS the
    handshake before ``accept()`` if the caller isn't an admin. Bearer
    tokens are not supported.
    """
    from logic import ssh as _ssh
    # ---- 1) Cookie auth — manual because Depends(require_admin) doesn't
    #       apply to WebSocket routes.
    user = None
    cookie = websocket.cookies.get(auth.COOKIE_NAME)
    if cookie:
        token_id = auth.parse_session_cookie(cookie)
        if token_id:
            try:
                with db_conn() as c:
                    sess = auth.get_active_session(c, token_id)
                    if sess:
                        u = auth.get_user(c, sess["user_id"])
                        if u and not u.disabled:
                            user = u
            except Exception as e:
                print(f"[ssh] terminal auth lookup failed: {e}")
    if user is None:
        # 4401 — RFC-6455 application close-code (4xxx is private use).
        # Starlette rejects the upgrade with HTTP 403 when ``close()`` is
        # called before ``accept()``; that's fine — the browser reads
        # the failed-handshake event and we never burn an audit row on a
        # bogus session. The SPA maps either signal to "session expired".
        await websocket.close(code=4401, reason="auth required")
        return
    if user.role != "admin":
        await websocket.close(code=4403, reason="admin required")
        return

    # ---- 1.5) Origin gate — defence-in-depth against CSWSH. FastAPI's
    #        WebSocket upgrades skip the HTTP middleware's CSRF path,
    #        so admin-only WS routes can't rely on the same
    #        double-submit cookie protection HTTP routes get. The
    #        session cookie's ``SameSite=lax`` attribute blocks most
    #        cross-site WS upgrades on Chromium / Firefox, but
    #        subdomain attacks and custom proxy setups can still
    #        leak the cookie. Reject the upgrade when the browser-
    #        supplied Origin doesn't match the resolved server
    #        origin. ``Origin`` may be empty for some non-browser
    #        callers (e.g. command-line tools that explicitly bypass
    #        it); we treat empty as "no claim made" and accept it
    #        since the admin cookie + role gate already rejected
    #        unauthenticated callers — the Origin gate is purely a
    #        browser-CSWSH defence and a missing header isn't one of
    #        those attack shapes.
    browser_origin = (websocket.headers.get("origin") or "").strip().lower()
    if browser_origin:
        expected_origin = _request_origin(websocket).strip().lower()
        if browser_origin != expected_origin:
            print(
                f"[ssh] terminal Origin mismatch: browser={browser_origin!r} "
                f"expected={expected_origin!r} host_id={host_id!r} user={user.username!r}"
            )
            await websocket.close(code=4403, reason="origin mismatch")
            return

    actor = user.username

    # ---- 2) Resolve SSH params + open the shell.
    hosts_config = _load_hosts_config()
    # Optional initial geometry from the upgrade query string. xterm.js
    # ships a saner first-frame with "actual cols/rows" once it mounts,
    # so this is just a best-guess so the prompt isn't 80x24 for the
    # first redraw on widescreen monitors.
    try:
        init_cols = int(websocket.query_params.get("cols") or 80)
    except (TypeError, ValueError):
        init_cols = 80
    try:
        init_rows = int(websocket.query_params.get("rows") or 24)
    except (TypeError, ValueError):
        init_rows = 24

    await websocket.accept()
    started_at = time.time()
    audit_row_id: Optional[int] = None
    bytes_in = 0  # browser -> shell
    bytes_out = 0  # shell -> browser
    final_status = "success"
    final_error: Optional[str] = None
    conn = None
    proc = None

    try:
        try:
            conn, proc, resolved = await _ssh.open_shell(
                host_id, hosts_config,
                term_cols=init_cols, term_rows=init_rows,
            )
        except _ssh.TerminalConfigError as e:
            await websocket.send_json({
                "type": "error",
                "code": getattr(e, "code", "config"),
                "message": str(e),
            })
            await websocket.close(code=4400, reason=getattr(e, "code", "config"))
            return
        except _ssh.TerminalAuthError as e:
            await websocket.send_json({
                "type": "error",
                "code": getattr(e, "code", "auth_failed"),
                "message": str(e),
            })
            await websocket.close(code=4401, reason="auth_failed")
            return
        except (asyncio.TimeoutError, TimeoutError):
            await websocket.send_json({
                "type": "error", "code": "timeout",
                "message": "SSH connection timed out",
            })
            await websocket.close(code=4500, reason="timeout")
            return
        except Exception as e:
            await websocket.send_json({
                "type": "error",
                "code": "connect_failed",
                "message": f"{type(e).__name__}: {e}",
            })
            await websocket.close(code=4500, reason="connect_failed")
            return

        audit_row_id = _ssh_terminal_audit_open(
            host_id=host_id, actor=actor, resolved=resolved,
        )

        # Surface the resolved target back to the SPA so the modal
        # footer can render "user@host:port · SHA256:abc..."
        await websocket.send_json({
            "type": "ready",
            "resolved": {
                "user": resolved.get("user"),
                "host": resolved.get("host"),
                "port": resolved.get("port"),
                "key_fingerprint": resolved.get("key_fingerprint", ""),
                "server_key_fingerprint": resolved.get("server_key_fingerprint", ""),
            },
        })

        # ---- 3) Pump bytes both ways + heartbeat ping. ----
        stop_event = asyncio.Event()

        # noinspection PyUnresolvedReferences
        async def upstream_to_ws():
            """Read shell stdout, send as binary WS frames."""
            nonlocal bytes_out
            try:
                while True:
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        # EOF — shell exited.
                        break
                    if isinstance(chunk, str):
                        chunk = chunk.encode(errors="replace")
                    bytes_out += len(chunk)
                    await websocket.send_bytes(chunk)
            except (asyncio.CancelledError, asyncssh.DisconnectError,
                    asyncssh.Error, BrokenPipeError, ConnectionResetError):
                pass
            except (RuntimeError, OSError, ValueError) as upstream_err:
                print(f"[ssh] terminal upstream_to_ws error: "
                      f"{type(upstream_err).__name__}: {upstream_err}")
            finally:
                stop_event.set()

        # noinspection PyTypeChecker,PyUnresolvedReferences
        async def ws_to_upstream():
            """Read WS frames, write to shell stdin or handle controls."""
            nonlocal bytes_in
            try:
                while True:
                    msg = await websocket.receive()
                    if msg.get("type") == "websocket.disconnect":
                        break
                    if "bytes" in msg and msg["bytes"] is not None:
                        data: bytes = msg["bytes"]
                        bytes_in += len(data)
                        try:
                            proc.stdin.write(data)
                        except (BrokenPipeError, ConnectionResetError):
                            break
                    elif "text" in msg and msg["text"] is not None:
                        # Control message — JSON-decoded.
                        try:
                            ctl = json.loads(msg["text"])
                        except (TypeError, ValueError):
                            continue
                        kind = (ctl or {}).get("type")
                        if kind == "resize":
                            _ssh.resize_shell(
                                proc,
                                ctl.get("cols", 80),
                                ctl.get("rows", 24),
                            )
                        elif kind == "ping":
                            # No-op — server pings are separate.
                            continue
                        elif kind == "stdin":
                            # Optional text-mode stdin (some clients
                            # prefer encoding via JSON). Keys "data".
                            data_s = (ctl or {}).get("data") or ""
                            data_b = data_s.encode(errors="replace")
                            bytes_in += len(data_b)
                            try:
                                proc.stdin.write(data_b)
                            except (BrokenPipeError, ConnectionResetError):
                                break
            except WebSocketDisconnect:
                pass
            except (asyncio.CancelledError, asyncssh.DisconnectError,
                    asyncssh.Error, BrokenPipeError, ConnectionResetError):
                pass
            except (RuntimeError, OSError, ValueError) as ws_err:
                print(f"[ssh] terminal ws_to_upstream error: "
                      f"{type(ws_err).__name__}: {ws_err}")
            finally:
                stop_event.set()

        async def heartbeat():
            """WS ping cadence (TUNABLE — `tuning_ssh_ws_heartbeat_seconds`)
            so idle proxies don't drop us. Resolved per-iteration so an
            Admin → Config save takes effect on the NEXT tick without a
            terminal reconnect."""
            try:
                while not stop_event.is_set():
                    await asyncio.sleep(tuning.tuning_int(Tunable.SSH_WS_HEARTBEAT_SECONDS))
                    if stop_event.is_set():
                        break
                    try:
                        # Starlette's WebSocket doesn't expose a public
                        # ping; fall back to a JSON keepalive frame the
                        # client can ignore. Keeps any L7 proxy from
                        # dropping the idle TCP socket.
                        await websocket.send_json({"type": "keepalive", "ts": time.time()})
                    except (RuntimeError, OSError, WebSocketDisconnect):
                        break
            except asyncio.CancelledError:
                pass

        t1 = asyncio.create_task(upstream_to_ws(), name="ssh-term-up")
        t2 = asyncio.create_task(ws_to_upstream(), name="ssh-term-dn")
        t3 = asyncio.create_task(heartbeat(), name="ssh-term-hb")
        try:
            await stop_event.wait()
        finally:
            for t in (t1, t2, t3):
                if not t.done():
                    t.cancel()
            # Drain cancellations.
            for t in (t1, t2, t3):
                try:
                    await t
                except (asyncio.CancelledError, RuntimeError, OSError):
                    pass

        # Try to harvest the shell's exit code so the close frame can
        # surface "exit 0" vs "exit 1". asyncssh exposes this on the
        # process once the channel closes.
        try:
            exit_code: int | None = proc.exit_status
        except AttributeError:
            exit_code = None
        if exit_code not in (None, 0):
            final_error = f"shell exited with code {exit_code}"
        try:
            await websocket.send_json({"type": "exit", "code": exit_code})
        except (RuntimeError, OSError, WebSocketDisconnect):
            pass
        try:
            await websocket.close(reason="shell exited")
        except (RuntimeError, OSError, WebSocketDisconnect):
            pass
    except WebSocketDisconnect:
        # Normal browser-side close (tab closed / network blip). Not an
        # error; final_status stays "success".
        pass
    except (asyncssh.Error, RuntimeError, OSError, ValueError) as sess_err:
        final_status = "failed"
        final_error = f"{type(sess_err).__name__}: {sess_err}"
        print(f"[ssh] terminal session ERROR host={host_id!r}: {sess_err}")
        try:
            await websocket.close(code=4500, reason="internal_error")
        except (RuntimeError, OSError, WebSocketDisconnect):
            pass
    finally:
        # Always close the upstream SSH connection.
        if proc is not None:
            try:
                proc.close()
            except (RuntimeError, OSError, AttributeError):
                pass
        if conn is not None:
            try:
                conn.close()
            except (RuntimeError, OSError, AttributeError):
                pass
            # Per-use read of the SSH conn-close timeout TUNABLE so a
            # Save in Admin → SSH takes effect on the next session
            # teardown without restart. Defensive fallback to legacy 5s
            # on tunable-resolver failure.
            try:
                _ssh_close_to = float(tuning.tuning_int(Tunable.SSH_CLOSE_TIMEOUT_SECONDS))
            except (ValueError, TypeError, KeyError):
                _ssh_close_to = 5.0
            try:
                await asyncio.wait_for(conn.wait_closed(), timeout=_ssh_close_to)
            except (asyncio.TimeoutError, OSError, RuntimeError):
                pass
        _ssh_terminal_audit_close(
            row_id=audit_row_id,
            started_at=started_at,
            status=final_status,
            error=final_error,
            bytes_in=bytes_in,
            bytes_out=bytes_out,
        )
        print(
            f"[ssh] terminal CLOSE host_id={host_id!r} actor={actor!r} "
            f"status={final_status} bytes_in={bytes_in} bytes_out={bytes_out} "
            f"duration={time.time() - started_at:.1f}s"
        )


# Re-export asyncssh for the WS handler's exception handling without
# forcing every other module to import the whole package.
import asyncssh  # noqa: E402,F401  (used inside ws_ssh_terminal handlers)


def _bucket_drawer_series(series: list, hours: int, target_points: int = 120) -> list:
    """Generic time-series bucketing for drawer-chart endpoints.

    Takes a list of dicts where each dict has a `t` (or `ts`) epoch-
    seconds field plus arbitrary metric fields. Returns a bucketed list
    of ~``target_points`` points evenly spread across the window. Field
    handling per-bucket:

    - **scalar numeric** (int/float, not bool): averaged across samples.
    - **dict of numeric leaves** (e.g. Beszel `temps: {cpu_thermal: 49}`):
      averaged per-leaf so the chart's per-sensor lines still render.
      Sensor keys that appear in only some samples in the bucket are
      averaged across the samples that have them.
    - **list of numeric elements** (e.g. `cpus: [10, 10, 8]`,
      `la: [0.19, 0.3, 0.43]`): element-wise averaged; output list
      length = max input length, missing positions averaged across the
      samples that had them.
    - **anything else** (list of dicts, dict of dicts, strings, bools,
      JSON blobs): last-in-bucket wins so the structural shape survives
      and the chart's downstream consumers still find their nested keys.
      Slight fidelity loss vs averaging but the chart STAYS FUNCTIONAL
      across 24h / 7d windows (pre-fix the field was dropped entirely
      and the chart fell through to "Collecting data").

    Short windows (≤2h) AND already-small series (len ≤ target) skip
    the bucket pass entirely — they're already chart-friendly. Buckets
    with no scalar-numeric data AND no dict/list metric data are
    dropped so the SPA's time-based gap detection renders them as real
    breaks in the line.

    Sampler-floor + min-bucket-width is 60s so we never produce a
    bucket smaller than a typical sampler tick.
    """
    if not series or hours <= 2 or len(series) <= target_points:
        return series
    bucket_s = max(60, int((hours * 3600) / target_points))

    # Discover field-kind by scanning the WHOLE series for the first
    # non-empty value per key. Pre-fix this only looked at sample[0] —
    # fields that are sparse-populated (Beszel `temps` / `gpus` — agent
    # omits the field on ticks where the sensor wasn't readable) got
    # classified as "other" when sample[0] happened to be empty / None,
    # then last-in-bucket wins routed empty {} into output buckets and
    # the chart saw zero sensors.
    #
    # Per-key kind classification:
    #   "scalar" → sum + count, AVG at emit
    #   "dict"   → per-leaf sum + count, AVG dict at emit
    #   "list"   → per-index sum + count, AVG list at emit
    #   "other"  → last-in-bucket wins (kept for structural fields like
    #              list-of-dicts e.g. `gpus`, bool flags, strings)
    def _classify_key(series_local: list, key: str) -> str:
        """Walk series_local sample-by-sample looking for the first
        non-null value at `key`; return one of scalar/dict/list/other."""
        for sample in series_local:
            if not isinstance(sample, dict):
                continue
            val = sample.get(key)
            if val is None:
                continue
            if isinstance(val, bool):
                return "other"
            if isinstance(val, (int, float)):
                return "scalar"
            if isinstance(val, dict):
                # Need at least ONE leaf to classify as dict-of-numerics.
                # All values must be numeric.
                if not val:
                    continue  # empty dict — keep scanning for a populated tick
                if all(isinstance(x, (int, float)) and not isinstance(x, bool)
                       for x in val.values()):
                    return "dict"
                return "other"
            if isinstance(val, list):
                if not val:
                    continue  # empty list — keep scanning
                if all(isinstance(x, (int, float)) and not isinstance(x, bool)
                       for x in val):
                    return "list"
                return "other"
            return "other"
        # Entirely empty / null across the whole series — "other" so an
        # empty value lands in `other_last` and the row stays consistent
        # with the source.
        return "other"

    # Collect every key that appears in ANY sample, not just sample[0].
    all_keys: set[str] = set()
    for r in series:
        if isinstance(r, dict):
            for k in r.keys():
                if k not in ("t", "ts"):
                    all_keys.add(k)
    kinds: dict[str, str] = {k: _classify_key(series, k) for k in all_keys}
    # Homogeneous accumulator: every value in `b` is itself a dict, so
    # PyCharm narrows sub-key accesses to dict (not the dict|int union
    # the mixed-shape version inferred). last_ts lives in a parallel
    # dict keyed on bucket-start-ts so it can stay typed as int without
    # bleeding union types into `b`.
    # Unannotated bare-dict (rather than dict[int, dict[str, Any]])
    # because PyCharm's strict-mode inference produced spurious
    # "Expected type 'int', got 'str'" warnings on the sub-key writes
    # — the annotation propagated the outer-key int type into the
    # inner accumulator's key inference. Bare `dict` keeps inference
    # off entirely. Runtime correctness is unchanged.
    buckets: dict = {}
    bucket_last_ts: dict[int, int] = {}
    for r in series:
        ts = int(r.get("t") or r.get("ts") or 0)
        if not ts:
            continue
        bts = (ts // bucket_s) * bucket_s
        b = buckets.get(bts)
        if b is None:
            b = {
                "scalar_sum": {}, "scalar_n": {},
                "dict_sum": {}, "dict_n": {},
                "list_sum": {}, "list_n": {},
                "other_last": {},
            }
            buckets[bts] = b
        # last_ts tracks the latest raw ts inside the bucket so the
        # emitted point lands at the newest sample inside the bucket
        # rather than the bucket center — chartFreshness on the SPA
        # reads the chart's tail to decide age; centre-emit made
        # wider windows misleadingly stale even when the most recent
        # raw sample was seconds old.
        prev_last = bucket_last_ts.get(bts, 0)
        if ts > prev_last:
            bucket_last_ts[bts] = ts
        for k, kind in kinds.items():
            v = r.get(k)
            if v is None:
                continue
            if kind == "scalar":
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                b["scalar_sum"][k] = b["scalar_sum"].get(k, 0.0) + fv
                b["scalar_n"][k] = b["scalar_n"].get(k, 0) + 1
            elif kind == "dict" and isinstance(v, dict):
                ds = b["dict_sum"].setdefault(k, {})
                dn = b["dict_n"].setdefault(k, {})
                for leaf, lv in v.items():
                    try:
                        flv = float(lv)
                    except (TypeError, ValueError):
                        continue
                    ds[leaf] = ds.get(leaf, 0.0) + flv
                    dn[leaf] = dn.get(leaf, 0) + 1
            elif kind == "list" and isinstance(v, list):
                ls = b["list_sum"].setdefault(k, [])
                ln = b["list_n"].setdefault(k, [])
                for idx, lv in enumerate(v):
                    try:
                        flv = float(lv)
                    except (TypeError, ValueError):
                        continue
                    while len(ls) <= idx:
                        ls.append(0.0)
                        ln.append(0)
                    ls[idx] += flv
                    ln[idx] += 1
            else:
                # "other" → last-in-bucket wins. Iteration order through
                # `series` is oldest-first so the final write IS the
                # latest sample in the bucket.
                b["other_last"][k] = v
    out: list = []
    for bts in sorted(buckets.keys()):
        b = buckets[bts]
        # Drop fully-empty buckets — every kind contributed zero data.
        # Allows the SPA's gap-detection to surface the gap honestly.
        if (
            not any(n > 0 for n in b["scalar_n"].values())
            and not b["dict_sum"]
            and not b["list_sum"]
            and not b["other_last"]):
            continue
        row: dict = {}
        for k, n in b["scalar_n"].items():
            row[k] = (b["scalar_sum"][k] / n) if n > 0 else 0
        for k, ds in b["dict_sum"].items():
            dn = b["dict_n"].get(k, {})
            row[k] = {
                leaf: (ds[leaf] / dn[leaf]) if dn.get(leaf, 0) > 0 else 0
                for leaf in ds.keys()
            }
        for k, ls in b["list_sum"].items():
            ln = b["list_n"].get(k, [])
            row[k] = [
                (ls[i] / ln[i]) if i < len(ln) and ln[i] > 0 else 0
                for i in range(len(ls))
            ]
        for k, v in b["other_last"].items():
            row[k] = v
        # Emit at the latest raw ts inside the bucket (NOT bucket
        # center) so the chart's tail = age of the freshest sample.
        # See the bucket-init comment above for the why.
        emit_ts = bucket_last_ts.get(bts, 0) or bts
        row["t"] = emit_ts
        row["ts"] = emit_ts
        out.append(row)
    return out


@app.get("/api/hosts/history")
async def api_hosts_history(system_id: str = "", hours: int = 1, host_id: str = ""):
    """Return time-series stats for one host.

    Powers the Hosts tab's per-row charts (CPU / Memory / Disk / Net).
    Two paths:

    1. ``system_id`` non-empty → BESZEL path. The system_id is Beszel's
       PocketBase record id — the frontend pulls it off the host row
       returned by :func:`api_hosts`. ``host_id`` (the curated
       hosts_config id) is used as a fallback key to layer in
       ``nr``/``ns`` from ``host_net_samples`` when Beszel's nr/ns are
       all zero (operator forgot ``NICS=eth0`` on the agent).

    2. ``system_id`` empty AND ``host_id`` non-empty → NODE-EXPORTER
       path. Reads pre-sampled rows from ``host_metrics_samples``
       (populated by ``logic.host_metrics_sampler``) and shapes them
       into the same series envelope Beszel returns, so the SPA's chart
       helpers work unchanged. Lets node-exporter-only hosts (no Beszel
       agent at all) get historical CPU / Memory / Disk / Network
       charts in the host drawer.
    """
    h = max(1, min(168, int(hours)))
    sid = (system_id or "").strip()
    hid = (host_id or "").strip()

    if not sid and hid:
        # NE / Pulse path — dispatch on which sampler has rows for
        # this host. Beszel-only hosts come through the system_id
        # branch below; the host_id branch is for hosts whose
        # primary surface is node-exporter OR Pulse.
        #
        # Resolution order:
        # 1. Try host_metrics_sampler first (NE-only host) — most
        #    common case on this branch.
        # 2. Fall through to host_pulse_sampler when the curated
        #    row has a `pulse_name` AND the NE table has no rows
        #    for this host. Pulse-only hosts (Proxmox VMs without
        #    a Beszel agent or node-exporter) land here so the
        #    SPA's chart helpers + inline sparkline see the same
        #    Beszel-compatible series envelope.
        from logic import host_metrics_sampler as _hms
        try:
            series = _hms.history_series(hid, h)
            collectors = _hms.series_collectors_present(hid, h)
        except Exception as e:
            series = []
            collectors = {}
            ne_err: Optional[str] = f"host_metrics_sampler: {e}"
        else:
            ne_err = None
        # Provider fallback chain — only consult downstream samplers
        # when NE has nothing AND the curated row carries the matching
        # provider's identifier. Avoids unnecessary queries on an
        # NE-only host that's temporarily empty (no need to mask
        # "host is idle" with a confusing Pulse / Webmin zero).
        # Order: Pulse → Webmin. Pulse first because Pulse-only hosts
        # are more common (Proxmox VMs); Webmin-only hosts are rare.
        if not series:
            try:
                curated = _load_hosts_config()
            except (json.JSONDecodeError, ValueError, OSError):
                curated = []
            row = next((r for r in curated if r.get("id") == hid), None)
            if row and (row.get("pulse_name") or "").strip():
                from logic import host_pulse_sampler as _hps
                try:
                    pseries = _hps.history_series(hid, h)
                except Exception as e:
                    return {"series": [], "error": f"host_pulse_sampler: {e}"}
                if pseries:
                    return {
                        "series": _bucket_drawer_series(pseries, h),
                        "collectors": {"cpu": True, "mem": True, "fs": True, "net": True, "disk_io": False},
                        "source": "pulse",
                        "error": None,
                    }
            # Webmin fallback. Curated row carries `webmin_name`
            # OR has a `webmin_url` mapped via `webmin_aliases`. Either
            # signal qualifies the host for Webmin history lookup.
            try:
                webmin_aliases = json.loads(get_setting(Settings.WEBMIN_ALIASES, "{}") or "{}")
                if not isinstance(webmin_aliases, dict):
                    webmin_aliases = {}
            except ValueError:
                webmin_aliases = {}
            if row and (
                (row.get("webmin_name") or "").strip()
                or (webmin_aliases.get(hid) or "").strip()
            ):
                from logic import host_webmin_sampler as _hws
                try:
                    wseries = _hws.history_series(hid, h)
                except Exception as e:
                    return {"series": [], "error": f"host_webmin_sampler: {e}"}
                if wseries:
                    return {
                        "series": _bucket_drawer_series(wseries, h),
                        "collectors": {"cpu": True, "mem": True, "fs": True, "net": True, "disk_io": False},
                        "source": "webmin",
                        "error": None,
                    }
        if ne_err:
            return {"series": [], "error": ne_err}
        return {"series": _bucket_drawer_series(series, h), "collectors": collectors, "error": None}

    if not sid:
        return {"series": [], "error": "system_id or host_id required"}

    # Beszel chart series — LOCAL ONLY.
    #
    # Architectural alignment with every other provider (Pulse /
    # Webmin / NE / SNMP / Ping): charts read exclusively from the
    # local sample table. The lifespan `host_beszel_sampler` is the
    # ETL — it reads from the Beszel hub on its tick cadence and
    # writes to `host_beszel_samples`. The chart endpoint never
    # touches the hub; it just queries the local DB.
    #
    # Trade-offs explicitly accepted with this design:
    #   1. Granularity. Local sampler ticks every
    #      `tuning_stats_sample_interval_seconds` (default 300s = 5
    #      min); hub's `1m` aggregation tier had 1-minute samples.
    #      Result: a 1h chart shows ~12 points (local) instead of 60
    #      (hub). Operator can lower the sampler interval to 60s for
    #      1m granularity at the cost of 5x DB writes — change via
    #      `tuning_beszel_sample_interval_seconds` (per-Beszel
    #      override) OR `tuning_stats_sample_interval_seconds` (the
    #      global fallback).
    #   2. Warm-up gap. A fresh deploy / fresh provider enable means
    #      `host_beszel_samples` is empty until the sampler ticks
    #      enough times to fill the requested window. Charts show
    #      the partial range that local covers (no hub fallback).
    #      Same behaviour as Pulse / Webmin / NE / SNMP / Ping local-
    #      only paths — operator-validated as the right design over
    #      the live-hub-fetch fallback (which created visible chart
    #      cuts when the hub's `1m` aggregator lagged independently
    #      of the agent's pushes).
    #   3. Long-range windows (> `tuning_stats_history_days`,
    #      default 7). Local retention is bounded; the hub had
    #      higher-tier aggregations (`120m`) with longer history.
    #      For windows beyond the local retention, the chart returns
    #      empty. If long-range becomes a real ask, reintroduce the
    #      hub fetch as an opt-in for windows > local retention.
    #      `logic/beszel.py:fetch_system_history` is kept in-tree as
    #      dead code for that future need.
    if not hid:
        return {"series": [], "error": "host_id required for Beszel local-only path"}
    from logic import host_beszel_sampler as _hbs
    try:
        local_series = _hbs.history_series(hid, h)
    except Exception as e:  # noqa: BLE001
        return {"series": [], "error": f"host_beszel_sampler: {e}"}
    return {
        "series": _bucket_drawer_series(local_series, h),
        "source": "beszel_local",
        "error": None,
    }


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.get("/api/hosts/{host_id}/ping/history")
async def api_hosts_ping_history(
    host_id: str, hours: int = 1,
    *,
    _admin: AdminUser,
):
    """Ping reachability time-series for one curated host.

    Mirrors :func:`api_hosts_history` shape — returns
    ``{points: [...], error: None}``. Empty list when this host has
    never been probed (sampler hasn't run yet, or the host isn't opted
    in). Window clamped to 1..168 hours like the Beszel path.

    **Bucketing** — raw `ping_samples` rows at 60s cadence produce
    ~1440 points in a 24h window, far more than the 420px-wide drawer
    chart can render usefully. The chart compresses to ~3 points per
    pixel and every sampler-blip-driven micro-gap surfaces as a broken
    line. Server-side bucketing produces a uniform ~120-point series
    regardless of window: bucket size = max(60s, ceil(hours×3600/120)).
    The bucket aggregator emits AVG(rtt_ms) for alive samples in the
    bucket (None when the whole bucket is dead, so the polyline's
    skip-don't-synthesize logic renders it as a real gap), majority
    `alive` flag, and AVG(loss_pct). Bucket midpoint timestamp lets
    the SPA's gap-detection adapt — at 12min buckets the gap threshold
    auto-derives to ~30min, hiding sub-bucket sampler noise that
    isn't actionable. Small windows (≤2h) skip the bucket pass since
    raw samples are already chart-friendly.
    """
    h = max(1, min(168, int(hours or 1)))
    hid = (host_id or "").strip()
    if not hid:
        return {"points": [], "error": "host_id required"}
    from logic import ping_sampler as _ping_sampler
    since = int(time.time() - h * 3600)
    # Read enough raw samples to cover the window cleanly even when the
    # sampler ran below its cadence (e.g. operator turned ping interval
    # down to 30s). 90 samples/hour × hours = headroom for the bucket
    # aggregator without truncating recent data.
    raw_limit = max(120, h * 90)
    try:
        rows = _ping_sampler.recent_samples(hid, since, limit=raw_limit)
    except Exception as e:
        return {"points": [], "error": f"ping_sampler: {e}"}
    # Small windows (≤2h) — return raw. 1h = ~60 points, 2h = ~120.
    # Below the target density anyway; bucketing would round-trip-distort
    # without helping rendering.
    target_points = 120
    if h <= 2 or len(rows) <= target_points:
        return {"points": rows, "error": None}
    bucket_s = max(60, int((h * 3600) / target_points))
    buckets: dict[int, dict] = {}
    for r in rows:
        ts = int(r.get("ts") or 0)
        if not ts:
            continue
        # Floor to bucket-start.
        bts = (ts // bucket_s) * bucket_s
        b = buckets.get(bts)
        if b is None:
            b = {"rtt_sum": 0.0, "rtt_n": 0,
                 "alive_n": 0, "total_n": 0,
                 "loss_sum": 0.0,
                 "rtt_min_min": None, "rtt_max_max": None,
                 # Track the latest raw ts that landed in this bucket
                 # so the emitted point lands at "newest sample
                 # inside the bucket" rather than the bucket midpoint.
                 # chartFreshness(h) on the SPA walks every cache slot
                 # picking MAX — midpoint-emit made the wider-window
                 # Ping series read stale even when the latest raw
                 # probe was seconds old.
                 "last_ts": 0}
            buckets[bts] = b
        if ts > b["last_ts"]:
            b["last_ts"] = ts
        b["total_n"] += 1
        if r.get("alive"):
            b["alive_n"] += 1
            rtt = r.get("rtt_ms")
            if rtt is not None:
                b["rtt_sum"] += float(rtt)
                b["rtt_n"] += 1
        rmin = r.get("rtt_min_ms")
        rmax = r.get("rtt_max_ms")
        if rmin is not None:
            b["rtt_min_min"] = rmin if b["rtt_min_min"] is None else min(b["rtt_min_min"], rmin)
        if rmax is not None:
            b["rtt_max_max"] = rmax if b["rtt_max_max"] is None else max(b["rtt_max_max"], rmax)
        b["loss_sum"] += float(r.get("loss_pct") or 0.0)
    # Bucket emit timestamp = latest raw ts inside the bucket so
    # chartFreshness reflects the actual freshness of the source
    # regardless of window. Falls back to bucket-start when the
    # bucket somehow has no raw ts (shouldn't happen — every raw row
    # contributes its ts to last_ts in the accumulator loop above).
    points = []
    for bts in sorted(buckets.keys()):
        b = buckets[bts]
        total = b["total_n"] or 1
        # All-dead bucket — no alive sample to average. DROP from the
        # response entirely (don't emit `rtt_ms: null`): the absent
        # bucket creates a time-gap that the SPA's polyline gap-detection
        # picks up, rendering the period as a real break in the line.
        # This is symmetric with the "sampler missed N ticks" case —
        # both yield gaps; the operator reads either as "no usable
        # latency reading for this window."
        if b["rtt_n"] <= 0:
            continue
        rtt_avg = b["rtt_sum"] / b["rtt_n"]
        # Majority alive flag — bucket considered alive iff > 50% of
        # its samples reported alive. Mixed-alive buckets still emit
        # the rtt_avg (computed from alive samples only) so the line
        # reflects "average latency when reachable" across the window.
        alive_majority = b["alive_n"] * 2 > total
        points.append({
            "ts": b["last_ts"] or bts,
            "alive": alive_majority,
            "rtt_ms": rtt_avg,
            "rtt_min_ms": b["rtt_min_min"],
            "rtt_max_ms": b["rtt_max_max"],
            "loss_pct": b["loss_sum"] / total,
            # Surface bucket metadata for the SPA's gap-aware renderer
            # + future tooltip "average over N samples in this 12min
            # bucket" copy. Optional — consumers fall back gracefully.
            "_bucket_seconds": bucket_s,
            "_samples_in_bucket": total,
        })
    return {"points": points, "error": None, "bucket_seconds": bucket_s}


@app.get("/api/hosts/{host_id}/http-probe/history")
async def api_hosts_http_probe_history(
    host_id: str, hours: int = 1,
    *,
    _admin: AdminUser,
):
    """HTTP / TLS / DNS probe time-series for one curated host.

    Returns ``{series: [{t, url, latency_ms, status_ok,
    tls_expires_in_days}, ...], collectors: {...}, error: None}``
    bucketed via the standard `_bucket_drawer_series` helper so the
    chart tail lands at the latest raw-sample timestamp inside each
    bucket (the freshness-label contract — see CLAUDE.md). Window
    clamped to 1..168 hours.

    The ``series`` shape is a flat list of points (NOT a dict
    keyed by URL) — each point carries the URL it came from so the
    SPA can group client-side into one line per URL. Up to ~120
    buckets per URL after bucketing; with N URLs per host the
    response can grow to N×120 rows but the per-row payload is
    tiny (5 fields) so this stays well under 100KB even for
    pathological 20-URL hosts.

    Empty ``series`` when the sampler hasn't written for this host
    yet (newly enabled, master toggle off, no resolvable URLs).
    """
    from logic import host_http_sampler as _hp_sampler
    h = max(1, min(168, int(hours or 1)))
    hid = (host_id or "").strip()
    if not hid:
        return {"series": [], "collectors": {}, "error": "host_id required"}
    since = int(time.time() - h * 3600)
    # Raw row limit — at 5min cadence × ~10 URLs/host × 168h = ~20k
    # rows. Cap at 20k so the SQL query stays cheap; bucketing collapses
    # to ~120 per URL anyway.
    raw_limit = max(500, h * 600)
    try:
        rows = _hp_sampler.recent_samples(hid, since, limit=raw_limit)
    except Exception as e:  # noqa: BLE001
        return {"series": [], "collectors": {}, "error": f"host_http_sampler: {e}"}
    if not rows:
        return {"series": [], "collectors": {}, "error": None}
    # Group raw rows by URL so each URL's series can be independently
    # bucketed — uniform density per line regardless of how many URLs
    # the host probes.
    by_url: dict[str, list] = {}
    for r in rows:
        url = r.get("url") or ""
        if not url:
            continue
        by_url.setdefault(url, []).append({
            "t": int(r.get("ts") or 0),
            "url": url,
            "latency_ms": r.get("latency_ms"),
            "status_ok": bool(r.get("status_ok")),
            "tls_expires_in_days": r.get("tls_expires_in_days"),
        })
    series_out: list[dict] = []
    for url, pts in by_url.items():
        # Each URL gets its own pass through `_bucket_drawer_series` so
        # the bucketing handles per-URL density independently. The
        # helper already emits each bucket's point at the latest raw-ts
        # inside that bucket — the freshness-label contract holds.
        bucketed = _bucket_drawer_series(pts, h)
        series_out.extend(bucketed)
    # Sort by ts ascending overall so consumers don't need to. URL
    # grouping is preserved by the SPA via point.url, not by order.
    series_out.sort(key=lambda p: p.get("t") or 0)
    return {"series": series_out, "collectors": {"sample_count": len(rows), "urls": len(by_url)}, "error": None}


@app.post("/api/hosts/{host_id}/http-probe/test")
async def api_hosts_http_probe_test_row(
    host_id: str,
    _admin: AdminUser,
):
    """Per-row HTTP / TLS / DNS test — fires against THIS row's
    configured URLs.

    Backend resolves the URL list from `hosts_config[host_id]`
    using the same chain the sampler uses
    (``http_probe.urls`` override OR ``url + services[].url``
    fallback). Probes each URL via :func:`probe_http_health` under
    the existing :class:`Cooldown` protection so a misconfigured
    host can't lock its auth out. Does NOT write to
    ``host_http_samples`` — the test is a diagnostic affordance,
    not a sampler tick.

    Returns ``{results: [{url, ok, status_code, latency_ms,
    tls_expires_in_days, error}, ...], elapsed_ms, error}``.
    """
    from logic import http_probe as _http_probe
    from logic.host_http_sampler import curated_http_probe_hosts as _curated
    hid = (host_id or "").strip()
    if not hid:
        raise HTTPException(400, "host_id required")
    # Find the curated row for this host via the sampler's shared
    # URL-resolver helper. Keeps the sampler + manual-test paths in
    # sync on resolution rules (override vs fallback) without
    # duplicating the walk logic.
    matching = [h for h in _curated() if h.get("id") == hid]
    if not matching:
        return {
            "results": [],
            "elapsed_ms": 0,
            "error": "host has no HTTP probe URLs configured (enable http_probe + add URLs OR ensure url / services[].url is set)",
        }
    host_cfg = matching[0]
    urls = list(host_cfg.get("urls") or [])
    if not urls:
        return {"results": [], "elapsed_ms": 0, "error": "no URLs resolved for this host"}
    timeout = float(tuning.tuning_int(Tunable.HTTP_PROBE_TIMEOUT_SECONDS))
    dns_timeout = float(tuning.tuning_int(Tunable.HTTP_PROBE_DNS_TIMEOUT_SECONDS))
    content_match = host_cfg.get("content_match")
    codes = host_cfg.get("accepted_status_codes") or []
    verify_tls = bool(host_cfg.get("verify_tls", True))
    started = time.monotonic()
    # Probe each URL in parallel — same bounded shape the sampler uses.
    sem = asyncio.Semaphore(max(1, tuning.tuning_int(Tunable.HTTP_PROBE_CONCURRENCY)))

    async def _one(url: str) -> dict:
        async with sem:
            try:
                r = await _http_probe.probe_http_health(
                    url,
                    timeout=timeout,
                    dns_timeout=dns_timeout,
                    content_match=content_match,
                    accepted_status_codes=codes,
                    verify_tls=verify_tls,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                r = {
                    "ok": False,
                    "status_code": None,
                    "latency_ms": None,
                    "tls_expires_in_days": None,
                    "error": f"{type(e).__name__}: {str(e)[:120]}",
                }
            r["url"] = url
            return r

    results = await asyncio.gather(*(_one(u) for u in urls))
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {"results": results, "elapsed_ms": elapsed_ms, "error": None}


@app.get("/api/hosts/{host_id}/beszel/services")
async def api_hosts_beszel_services(
    host_id: str,
    _admin: AdminUser,
):
    """Per-unit Beszel systemd-services snapshot for one curated host.

    Surfaces the data the lifespan ``host_beszel_sampler`` writes
    into ``host_beszel_services``: one row per (host, unit) pair with
    ``state`` (systemd ActiveState enum: 0=active, 1=reloading,
    2=inactive, 3=failed, 4=activating, 5=deactivating), ``sub_state``
    (systemd SubState enum), ``last_seen_ts`` (most recent tick that
    observed the unit), and ``last_change_ts`` (most recent tick where
    the state value changed — drives "failed since 2h ago" copy in
    the drawer without keeping a transition log).

    Returns ``{services: [...], error: None}``. Failed units come
    first (state=3), then alphabetical by unit name. Hosts that have
    never been Beszel-probed OR whose Beszel agent doesn't track
    systemd units return an empty list.

    Distinct from the rolled summary on ``host.services`` (total /
    failed count / failed_names) which the host API row already
    carries — this endpoint is for the drawer's per-unit detail
    pane + the AI palette's "what's the state of nginx on web01?"
    questions.
    """
    hid = (host_id or "").strip()
    if not hid:
        raise HTTPException(400, "host_id required")
    from logic import host_beszel_sampler as _hbs
    try:
        services = _hbs.services_for_host(hid)
    except Exception as e:  # noqa: BLE001
        return {"services": [], "error": f"host_beszel_services: {e}"}
    return {"services": services, "error": None}


@app.get("/api/hosts/{host_id}/snmp/history")
async def api_hosts_snmp_history(
    host_id: str, hours: int = 1,
    *,
    _admin: AdminUser,
):
    """SNMP time-series for one curated host.

    Returns ``{points: [...], error: None}`` with one row per
    ``host_snmp_samples`` entry in the window. Each point carries
    ``ts``, ``cpu_per_core`` (parsed JSON list of int 0..100),
    ``cpu_used_pct`` (UCD ssCpuIdle-derived smoother value),
    ``load_1m`` / ``load_5m`` / ``load_15m`` (floats), ``mem_total`` /
    ``mem_used`` / ``mem_buffers`` / ``mem_cached`` / ``mem_free``
    (bytes). Empty list when this host has never been SNMP-probed.
    Window clamped to 1..168 hours.
    """
    import json as _json
    h = max(1, min(168, int(hours or 1)))
    hid = (host_id or "").strip()
    if not hid:
        return {"points": [], "error": "host_id required"}
    since = int(time.time() - h * 3600)
    # Target ~200 points per chart regardless of window. Raw samples at
    # the default 5-min cadence produce ~2000 points over the 7d window
    # — too many to render legibly, and the line reads as noise. For
    # `hours > 24` bucket the rows server-side via GROUP BY (ts/bucket)
    # so the SPA receives a smoothed, sensibly-spaced series. Beszel's
    # PocketBase already does this via its `_pick_stat_type` aggregation
    # tier; this brings the local sampler-backed providers in line.
    bucket = 0
    # Bucket more aggressively: any window where the natural raw count
    # exceeds the target density gets server-side aggregation. At the
    # default 5-min SNMP cadence (300s), 6h+ windows accumulate >72 raw
    # points and start crowding the 420px-wide SVG. Bucketing to a
    # uniform target of ~120 points hides micro-gaps and produces a
    # consistent visual density across 1h / 6h / 24h / 7d. The 2h
    # threshold below mirrors the ping endpoint — short windows stay
    # raw because they're already chart-friendly.
    if h > 2:
        target_points = 120
        bucket = max(60, int(h * 3600 / target_points))
    try:
        with db_conn() as c:
            if bucket > 0:
                # SQL-level bucketing — AVG numeric fields, drop the
                # JSON cpu_per_core (can't average a list cell). The
                # bucket key is `ts / bucket` integer-divided; we emit
                # the bucket's MIN(ts) as the canonical timestamp.
                rows = c.execute(
                    "SELECT MAX(ts) AS ts, NULL AS cpu_per_core, "
                    "AVG(cpu_used_pct) AS cpu_used_pct, "
                    "AVG(load_1m) AS load_1m, AVG(load_5m) AS load_5m, AVG(load_15m) AS load_15m, "
                    "AVG(mem_total) AS mem_total, AVG(mem_used) AS mem_used, "
                    "AVG(mem_buffers) AS mem_buffers, AVG(mem_cached) AS mem_cached, AVG(mem_free) AS mem_free, "
                    "MAX(uptime_s) AS uptime_s, "
                    "MAX(net_rx_total_bytes) AS net_rx_total_bytes, MAX(net_tx_total_bytes) AS net_tx_total_bytes, "
                    "MAX(printer_page_count) AS printer_page_count, "
                    "AVG(load_percent) AS load_percent, AVG(battery_percent) AS battery_percent, "
                    "AVG(battery_temp_c) AS battery_temp_c, "
                    "AVG(disk_total) AS disk_total, AVG(disk_used) AS disk_used "
                    "FROM host_snmp_samples "
                    "WHERE host_id=? AND ts >= ? "
                    "GROUP BY ts / ? "
                    "ORDER BY ts ASC LIMIT ?",
                    (hid, since, bucket, h * 60),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT ts, cpu_per_core, cpu_used_pct, "
                    "load_1m, load_5m, load_15m, "
                    "mem_total, mem_used, mem_buffers, mem_cached, mem_free, "
                    "uptime_s, net_rx_total_bytes, net_tx_total_bytes, "
                    "printer_page_count, load_percent, battery_percent, "
                    "battery_temp_c, disk_total, disk_used "
                    "FROM host_snmp_samples "
                    "WHERE host_id=? AND ts >= ? "
                    "ORDER BY ts ASC LIMIT ?",
                    (hid, since, h * 60),
                ).fetchall()
    except Exception as e:
        return {"points": [], "error": f"snmp_history: {e}"}
    points = []
    for r in rows:
        try:
            cores = _json.loads(r[1]) if r[1] else []
        except (ValueError, TypeError):
            cores = []
        points.append({
            "ts": int(r[0]),
            "cpu_per_core": cores,
            "cpu_used_pct": (float(r[2]) if r[2] is not None else None),
            "load_1m": (float(r[3]) if r[3] is not None else None),
            "load_5m": (float(r[4]) if r[4] is not None else None),
            "load_15m": (float(r[5]) if r[5] is not None else None),
            "mem_total": (int(r[6]) if r[6] is not None else None),
            "mem_used": (int(r[7]) if r[7] is not None else None),
            "mem_buffers": (int(r[8]) if r[8] is not None else None),
            "mem_cached": (int(r[9]) if r[9] is not None else None),
            "mem_free": (int(r[10]) if r[10] is not None else None),
            # uptime in seconds; NULL for pre-uptime-column rows.
            "uptime_s": (int(r[11]) if r[11] is not None else None),
            # cumulative IF-MIB ifHCInOctets / ifHCOutOctets
            # sums; NULL for pre-throughput-column rows or when SNMP didn't return
            # the counters (e.g. switch with hrStorage but no IF-MIB).
            # Chart layer computes per-pair deltas → bps; out-of-bounds
            # / negative deltas (counter wrap, reboot) are skipped.
            "net_rx_total_bytes": (int(r[12]) if r[12] is not None else None),
            "net_tx_total_bytes": (int(r[13]) if r[13] is not None else None),
            # printer lifetime page count (Printer-MIB
            # prtMarkerLifeCount). Cumulative monotonic counter; the
            # SPA computes deltas → pages-per-day.
            "printer_page_count": (int(r[14]) if r[14] is not None else None),
            # APC UPS time-series fields. NULL for non-UPS hosts
            # or pre-fix rows. Drives the Output Load / Battery /
            # Battery temperature charts in the host drawer's UPS card.
            "load_percent": (float(r[15]) if r[15] is not None else None),
            "battery_percent": (float(r[16]) if r[16] is not None else None),
            "battery_temp_c": (float(r[17]) if r[17] is not None else None),
            # Aggregate disk totals (bytes). Drives the Hosts-row
            # disk sparkline for SNMP-only hosts. NULL for pre-fix
            # rows; SPA computes percent as (used / total) * 100
            # and treats null/0 totals as "no signal".
            "disk_total": (int(r[18]) if r[18] is not None else None),
            "disk_used": (int(r[19]) if r[19] is not None else None),
        })
    # Surface `bucket_seconds` to the SPA so rate-computation helpers
    # (`snmpThroughputBpsSeries` / iface throughput / etc.) can scale
    # their gap-detection threshold to the actual server-side bucket
    # cadence. 0 when the response wasn't bucketed (≤2h windows pass
    # raw samples through). Without this, the SPA's static 3600s cap
    # rejected EVERY consecutive-pair delta on 7d windows (5040s
    # buckets) and the throughput chart fell through to "Collecting
    # data" forever.
    return {"points": points, "error": None, "bucket_seconds": bucket}


@app.get("/api/hosts/{host_id}/disk-projection")
async def api_hosts_disk_projection(
    host_id: str, hours: int = 720,
    *,
    _admin: AdminUser,
):
    """Linear-projection disk-fill forecast for one curated host.

    Reads `disk_used` / `disk_total` history from whichever sampler
    table has rows for this host (priority: ``host_metrics_samples``
    → ``host_pulse_samples`` → ``host_webmin_samples``). Computes
    ordinary-least-squares linear regression of ``used_pct`` over time;
    projects forward by the same window as the lookback. Confidence
    pill derives from R² + sample density:
        high   ≥ 0.85 R²  AND  ≥ 60 samples
        medium ≥ 0.60 R²  OR   ≥ 30 samples
        low    otherwise
    Exhaustion timestamp = first projected point where used_pct ≥ 100;
    None when slope ≤ 0 (host is shrinking / stable) OR projection
    stays under 100% within the forward window (operator has time).
    Lookback ``hours`` clamped 24..2160 (1d..90d).
    """
    h = max(24, min(2160, int(hours or 720)))
    hid = (host_id or "").strip()
    if not hid:
        return {"error": "host_id required"}
    since = int(time.time() - h * 3600)
    # Pick the sampler source whose latest `disk_total` is LARGEST
    # — that matches the canonical "this is what my pool looks like"
    # value the operator (and the AI palette context) sees on the
    # host row, rather than picking a root-disk-only NE slice when
    # the host's primary surface is a multi-TB Pulse-reported pool.
    # Pre-fix the priority order was (NE → Pulse → Webmin) first-wins
    # on count ≥ 5, which silently chose NE's root-disk view on a
    # PVE host whose actual storage is the ZFS pool reported via Pulse.
    # Operator-facing symptom: "AI says 96% / 975 GB but the chart
    # says 12% / 195 GB" — different sources, same host.
    sources = (
        ("host_metrics_samples", "node_exporter"),
        ("host_pulse_samples", "pulse"),
        ("host_webmin_samples", "webmin"),
    )
    candidates: list[tuple[str, list]] = []  # (label, rows) for each source with ≥ 5 rows
    try:
        with db_conn() as c:
            for table, label in sources:
                cur = c.execute(
                    f"SELECT ts, disk_used, disk_total FROM {table} "
                    f"WHERE host_id=? AND ts>=? AND disk_used IS NOT NULL "
                    f"  AND disk_total IS NOT NULL AND disk_total > 0 "
                    f"ORDER BY ts ASC",
                    (hid, since),
                )
                fetched = cur.fetchall()
                if len(fetched) >= 5:
                    candidates.append((label, fetched))
    except Exception as e:
        return {"error": f"db read failed: {e}", "samples": [], "projection": []}
    rows: list = []
    source_used = None
    if candidates:
        # Pick by largest most-recent `disk_total` — the "biggest pool"
        # heuristic. Tie-break by sample count (more samples = more
        # signal). The previous source-priority list is the final
        # fallback when totals + counts are identical.
        priority_order = {src_label: i for i, (_, src_label) in enumerate(sources)}

        def _rank(c_label_rows):
            """Sort key for candidate (label, rows) tuples — bigger
            latest total first, then more samples, then source-priority."""
            rank_label, rs = c_label_rows
            latest_total = int(rs[-1][2] or 0)
            return -latest_total, -len(rs), priority_order.get(rank_label, 999)

        candidates.sort(key=_rank)
        source_used, rows = candidates[0]
    if not rows:
        return {
            "host_id": hid,
            "samples": [],
            "projection": [],
            "exhaustion_ts": None,
            "confidence": "low",
            "current": None,
            "slope_pct_per_day": 0.0,
            "total_bytes": None,
            "source": None,
            "error": None,
        }
    # Build the (ts, used_pct, used_bytes, total_bytes) series.
    series = [
        (int(r[0]), (int(r[1]) / int(r[2])) * 100.0, int(r[1]), int(r[2]))
        for r in rows
    ]
    n = len(series)
    # OLS regression on (ts, used_pct). Anchor x at the first sample
    # so the magnitudes stay small (otherwise int(time.time())^2 in
    # the sums overflows float64 precision noticeably).
    t0 = series[0][0]
    xs = [(s[0] - t0) for s in series]
    ys = [s[1] for s in series]
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = (n * sxx) - (sx * sx)
    if denom == 0:
        slope = 0.0
        intercept = sy / n
    else:
        slope = (n * sxy - sx * sy) / denom
        intercept = (sy - slope * sx) / n
    # R² on the same series.
    mean_y = sy / n
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 0.0 if ss_tot == 0 else max(0.0, 1.0 - (ss_res / ss_tot))
    # Residual standard error + Sxx for the prediction-interval band.
    # The band widens as we extrapolate further from the historical
    # mean (`mean_x`) — it's the classical OLS forecast cone shape.
    # Uses 1.96 (≈ 95% one-tailed normal critical value) as the
    # multiplier; with n typically ≥ 30 the t-distribution is close
    # enough to the normal that the simpler constant reads cleanly.
    mean_x = sx / n
    sxx_dev = sum((x - mean_x) ** 2 for x in xs)
    sigma_resid = math.sqrt(max(0.0, ss_res) / max(1, n - 2)) if n >= 3 else 0.0
    # Confidence pill — needs both R² AND sample density.
    if r2 >= 0.85 and n >= 60:
        confidence = "high"
    elif r2 >= 0.60 or n >= 30:
        confidence = "medium"
    else:
        confidence = "low"
    # Exhaustion: solve intercept + slope * (t - t0) = 100 for t.
    exhaustion_ts: int | None = None
    if slope > 1e-12:
        t_exhaust_offset = (100.0 - intercept) / slope
        t_exhaust = t0 + t_exhaust_offset
        # Only meaningful if it's in the future (post-now) AND within
        # a "reasonable" forecast window (10× the lookback — beyond
        # that the linear-extrapolation assumption is too fragile).
        now_ts = int(time.time())
        max_horizon = now_ts + (h * 3600 * 10)
        if now_ts < t_exhaust < max_horizon:
            exhaustion_ts = int(t_exhaust)
    # Slope rendered as pct-per-day for the operator-friendly summary.
    slope_pct_per_day = slope * 86400.0
    # Build the response samples — downsample to ≤ 120 points so the
    # chart payload stays small. Take evenly-spaced indices.
    if n <= 120:
        out_samples = series
    else:
        step = n / 120.0
        idxs = sorted({int(i * step) for i in range(120)} | {n - 1})
        out_samples = [series[i] for i in idxs]
    samples_payload = [
        {"ts": s[0], "used_pct": round(s[1], 2),
         "used_bytes": s[2], "total_bytes": s[3]}
        for s in out_samples
    ]
    # Projection — same forward window length as lookback, ~30 points.
    # Each point carries the central prediction PLUS a 95% prediction
    # interval (low_pct / high_pct) so the SPA can render the classic
    # forecast-cone "fork" widening with extrapolation distance —
    # operators see uncertainty at a glance instead of a misleading
    # single-line prediction.
    forward_secs = h * 3600
    projection_payload = []
    proj_steps = 30
    now_ts = int(time.time())
    z = 1.96  # ≈ 95% one-tailed normal critical value
    for i in range(proj_steps + 1):
        t_offset = (forward_secs * i) / proj_steps
        ts_proj = now_ts + int(t_offset)
        x_proj = ts_proj - t0
        used_pct_proj = intercept + slope * x_proj
        # Standard error of prediction at this x — widens with
        # distance from the historical mean. When sxx_dev is 0 (all
        # samples at one timestamp, degenerate), fall back to a flat
        # band based on the residual std alone.
        if sxx_dev > 0 and n >= 3:
            se_pred = sigma_resid * math.sqrt(
                1.0 + (1.0 / n) + ((x_proj - mean_x) ** 2) / sxx_dev
            )
        else:
            se_pred = sigma_resid
        margin = z * se_pred
        used_pct_clamped = max(0.0, min(100.0, used_pct_proj))
        low_clamped = max(0.0, min(100.0, used_pct_proj - margin))
        high_clamped = max(0.0, min(100.0, used_pct_proj + margin))
        projection_payload.append({
            "ts": ts_proj,
            "used_pct": round(used_pct_clamped, 2),
            "low_pct": round(low_clamped, 2),
            "high_pct": round(high_clamped, 2),
        })
    last = series[-1]
    # Stale-data hint — the latest sample's age vs now. If the most
    # recent sample is older than the typical sampler interval × N,
    # the underlying provider has likely stopped reporting (paused /
    # auth failure / network outage) and the projection is operating
    # on a frozen snapshot. The frontend renders a badge when
    # `stale=True`. Threshold = 30 minutes; samplers run every 5 min
    # by default so anything older than 30 min is suspect.
    now_ts = int(time.time())
    last_ts = int(last[0] or 0)
    age_seconds = max(0, now_ts - last_ts) if last_ts > 0 else 0
    is_stale = age_seconds > 1800  # 30 minutes
    return {
        "host_id": hid,
        "samples": samples_payload,
        "projection": projection_payload,
        "exhaustion_ts": exhaustion_ts,
        "confidence": confidence,
        "current": {
            "ts": last[0],
            "used_pct": round(last[1], 2),
            "used_bytes": last[2],
            "total_bytes": last[3],
        },
        "slope_pct_per_day": round(slope_pct_per_day, 4),
        "total_bytes": last[3],
        "source": source_used,
        "lookback_hours": h,
        "r2": round(r2, 4),
        "sample_count": n,
        "stale": is_stale,
        "stale_age_seconds": age_seconds,
        "error": None,
    }


@app.get("/api/hosts/{host_id}/snmp/iface_history")
async def api_hosts_snmp_iface_history(
    host_id: str, hours: int = 1,
    *,
    _admin: AdminUser,
):
    """Per-interface SNMP counter history for one host.

    Returns ``{ifaces: {ifname: [points...]}, error: null}`` with one
    series per interface. Each point carries ``ts`` + ``in_bytes`` +
    ``out_bytes`` (cumulative IF-MIB counters; the chart layer
    computes per-pair deltas → bps with skip-don't-synthesize on
    out-of-bounds). Empty dict when this host has never been
    SNMP-probed for interfaces. Window clamped to 1..168 hours.
    """
    h = max(1, min(168, int(hours or 1)))
    hid = (host_id or "").strip()
    if not hid:
        return {"ifaces": {}, "error": "host_id required"}
    since = int(time.time() - h * 3600)
    # Server-side bucketing on long windows so the per-port chart
    # doesn't accumulate ~2000 points per iface × 5 ifaces ≈ 10k points
    # at 7d. Same shape as the SNMP main-history endpoint: bucket
    # threshold is `h > 2`, target ~120 points per iface, bucket
    # cadence = max(60, hours*3600/120). MAX of in_bytes / out_bytes /
    # link_speed_mbps in each bucket because the counters are
    # MONOTONIC — last-value-in-bucket is what the rate computation
    # needs.
    bucket = 0
    if h > 2:
        target_points = 120
        bucket = max(60, int(h * 3600 / target_points))
    try:
        with db_conn() as c:
            if bucket > 0:
                # Bucket key includes `ifname` so each interface gets
                # its own ~120-point series (otherwise the GROUP BY
                # would mix samples across ifaces in the same time
                # bucket).
                rows = c.execute(
                    "SELECT MAX(ts) AS ts, ifname, "
                    "MAX(in_bytes) AS in_bytes, MAX(out_bytes) AS out_bytes, "
                    "MAX(link_speed_mbps) AS link_speed_mbps "
                    "FROM host_snmp_iface_samples "
                    "WHERE host_id=? AND ts >= ? "
                    "GROUP BY ifname, ts / ? "
                    "ORDER BY ifname ASC, ts ASC LIMIT ?",
                    (hid, since, bucket, h * 60 * 64),
                ).fetchall()
            else:
                # Row-count ceiling — guard against runaway payload on a
                # 48-port switch × 168-hour window. h * 60 samples/hr × 64
                # ifaces is a safe upper bound; the index on (host_id, ts
                # DESC) makes this read fast but the JSON payload + chart-
                # build loop are still linear in row count.
                rows = c.execute(
                    "SELECT ts, ifname, in_bytes, out_bytes, link_speed_mbps "
                    "FROM host_snmp_iface_samples "
                    "WHERE host_id=? AND ts >= ? "
                    "ORDER BY ifname ASC, ts ASC LIMIT ?",
                    (hid, since, h * 60 * 64),
                ).fetchall()
    except Exception as e:
        return {"ifaces": {}, "error": f"snmp_iface_history: {e}"}
    ifaces: dict = {}
    for r in rows:
        ifname = r[1]
        if ifname not in ifaces:
            ifaces[ifname] = []
        ifaces[ifname].append({
            "ts": int(r[0]),
            "in_bytes": (int(r[2]) if r[2] is not None else None),
            "out_bytes": (int(r[3]) if r[3] is not None else None),
            # slice 4 — IF-MIB ifHighSpeed (Mbps); NULL when the
            # device doesn't expose it.
            "link_speed_mbps": (int(r[4]) if r[4] is not None else None),
        })
    # Surface `bucket_seconds` so the SPA's rate-computation helper
    # (`snmpIfaceBpsSeries`) can scale its dt cap to the bucket cadence.
    # 0 when the response wasn't bucketed (≤2h windows pass raw rows
    # through).
    return {"ifaces": ifaces, "error": None, "bucket_seconds": bucket}


@app.get("/api/hosts/{host_id}/snmp/temp_history")
async def api_hosts_snmp_temp_history(
    host_id: str, hours: int = 1,
    *,
    _admin: AdminUser,
):
    """Per-temperature-probe SNMP history for one host.

    Returns ``{probes: {probe_idx: {name, points: [...]}}, error: null}``
    with one series per probe (probe_idx is the trailing OID index,
    stable across ticks; probe_name is the human-readable label like
    "Inlet Temp" / "CPU1 Temp"). Each point carries ``ts`` + ``c``
    (degrees Celsius). Window clamped to 1..168 hours; row-count ceiling
    is hours × 60 × 16 (assume up to 16 probes per server which covers
    every Dell PowerEdge generation we've seen).
    """
    h = max(1, min(168, int(hours or 1)))
    hid = (host_id or "").strip()
    if not hid:
        return {"probes": {}, "error": "host_id required"}
    since = int(time.time() - h * 3600)
    # Server-side bucketing — same pattern as snmp_history / iface_history.
    # 7d × 12 samples/hr × 16 probes = ~32k raw points before bucketing.
    # Temperature readings are INSTANTANEOUS (not counters), so AVG per
    # bucket is the right aggregate. Threshold `h > 2`; target ~120
    # points per probe.
    bucket = 0
    if h > 2:
        target_points = 120
        bucket = max(60, int(h * 3600 / target_points))
    try:
        with db_conn() as c:
            if bucket > 0:
                # Bucket key includes `probe_idx` so each probe's series
                # stays separate. MAX(probe_name) picks any value within
                # the bucket — operator-renames are rare so consistency
                # across the bucket is preserved.
                rows = c.execute(
                    "SELECT MAX(ts) AS ts, probe_idx, "
                    "MAX(probe_name) AS probe_name, AVG(value_c) AS value_c "
                    "FROM host_snmp_temp_samples "
                    "WHERE host_id=? AND ts >= ? "
                    "GROUP BY probe_idx, ts / ? "
                    "ORDER BY probe_idx ASC, ts ASC LIMIT ?",
                    (hid, since, bucket, h * 60 * 16),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT ts, probe_idx, probe_name, value_c "
                    "FROM host_snmp_temp_samples "
                    "WHERE host_id=? AND ts >= ? "
                    "ORDER BY probe_idx ASC, ts ASC LIMIT ?",
                    (hid, since, h * 60 * 16),
                ).fetchall()
    except Exception as e:
        return {"probes": {}, "error": f"snmp_temp_history: {e}"}
    probes: dict = {}
    for r in rows:
        idx = str(r[1] or "")
        if not idx:
            continue
        name = r[2] or f"temp-{idx}"
        probe_bucket: dict = probes.setdefault(idx, {"name": name, "points": []})
        # Pick the freshest probe_name we've seen — operator-renamed
        # probes (rare) propagate forward this way.
        probe_bucket["name"] = name
        probe_bucket["points"].append({
            "ts": int(r[0]),
            "c": (float(r[3]) if r[3] is not None else None),
        })
    # Surface bucket cadence for consistency with the sibling endpoints
    # (SPA doesn't currently consume it for temp charts — temperatures
    # are instantaneous values not counter rates — but the field is
    # available for future symmetry).
    return {"probes": probes, "error": None, "bucket_seconds": bucket}


@app.get("/api/hosts/{host_id}/triage")
async def api_hosts_triage(
    host_id: str,
    hours: int = 720,
    *,
    _admin: AdminUser,
):
    """Similar-incidents grouping for one host. Walks history /
    notifications / host_failure_events for the last ``hours``
    (default 720 = 30 days, range 1..2160), classifies each error via
    `logic.triage._classify_error()` into one of ~10 buckets (timeout
    / auth / refused / dns / tls / not-found / server-error / network
    / parse / rate-limit / other), and groups by (provider, pattern).

    Each group returns: count, first_ts, last_ts, avg_duration_s
    (computed by pairing host_failure_events paused→recovered rows),
    sample_errors (capped at 3 distinct strings), occurrences (capped
    at 50 ts+error pairs). Sorted newest-first (last_ts DESC + count
    DESC as tiebreaker).

    Drives the host drawer's "Similar incidents" panel — operators
    used to scroll-hunt across History tab + the per-host Timeline +
    Admin → Logs to triangulate "is this the same problem"; the
    panel does the work upfront.
    """
    curated = _load_hosts_config()
    if not next((x for x in curated if x.get("id") == (host_id or "").strip()), None):
        raise HTTPException(404, f"Host not found: {host_id}")
    from logic import triage as _triage
    return _triage.triage_host(host_id, hours=hours)


@app.get("/api/hosts/{host_id}/timeline")
async def api_hosts_timeline(
    host_id: str,
    hours: int = 168,
    limit: int = 200,
    *,
    _admin: AdminUser,
):
    """Unified per-host event timeline for incident triage.

    Aggregates four signal sources keyed to ``host_id``:

    * ``history`` rows where ``target_id`` matches the host id OR the
      target name resolves to the host (covers the ``op_type='ssh_run'``
      / ``'snmp_resume'`` / etc. surfaces that target the host
      directly).
    * ``notifications`` rows whose ``target_kind == 'host'`` AND
      ``target_id == host_id``.
    * ``host_failure_state`` snapshots for both the bare host_id row
      and every per-provider ``<provider>:<host_id>`` row — these
      surface as ``provider_paused`` events keyed off the row's
      ``paused_at`` (or ``last_failure_ts`` when present).
    * ``host_provider_last_ok`` rows — surfaces as ``provider_recovered``
      events (last successful probe per provider).

    Returns the merged stream sorted newest-first with a unified
    envelope per event:

    .. code-block:: json

        {
          "events": [
            {
              "ts": 1714500000,
              "kind": "op",                  // op | notification | provider_paused | provider_recovered
              "severity": "success",         // success | error | warning | info
              "title": "Container update",
              "body": "stack/web — pulled :latest",
              "actor": "alice",
              "metadata": {...}
            }
          ],
          "counts": {"ops": 12, "notifications": 5, "failures": 2, "recoveries": 3},
          "host_id": "<id>",
          "hours": 168
        }
    """
    h = max(1, min(720, int(hours or 168)))  # 30-day max
    lim = max(10, min(2000, int(limit or 200)))
    hid = (host_id or "").strip()
    if not hid:
        raise HTTPException(400, "host_id required")
    curated = _load_hosts_config()
    row = next((r for r in curated if r.get("id") == hid), None)
    if row is None:
        raise HTTPException(404, f"Host not found: {hid}")
    # Display name candidates — used for fuzzy-matching history rows
    # whose `target_id` doesn't exactly match (e.g. older
    # ssh_run rows persisted target_name=hostname instead of host_id).
    # Free-form fields (label / *_name) can collide across hosts —
    # operator types "Web server" as the label of host A AND host B,
    # the OR clause downstream would surface B's history rows in A's
    # timeline. Filter the extras to only names THIS host has actually
    # used in past history rows (target_id=hid) so cross-host
    # collisions can't bleed across timelines. The bare hid is always
    # preserved; legacy rows with target_id=NULL but target_name=label
    # can still match iff the same label has been associated with this
    # host_id at some point.
    name_candidates: set[str] = {hid}
    extras: set[str] = set()
    for k in ("label", "snmp_name", "beszel_name", "pulse_name", "webmin_name"):
        v = (row.get(k) or "").strip()
        if v:
            extras.add(v)
    since = int(time.time() - h * 3600)
    events: list[dict] = []
    counts = {"ops": 0, "notifications": 0, "failures": 0, "recoveries": 0}

    try:
        with db_conn() as c:
            # Pre-filter the free-form name candidates. On a DB error,
            # fall back to including every extra (legacy behaviour) —
            # over-matching is preferable to under-matching on a
            # transient DB blip.
            if extras:
                try:
                    used_rows = c.execute(
                        "SELECT DISTINCT target_name FROM history "
                        "WHERE target_id=? AND target_name IS NOT NULL",
                        (hid,),
                    ).fetchall()
                    used = {r[0] for r in used_rows if r[0]}
                    name_candidates |= (extras & used)
                except (sqlite3.Error, ValueError, TypeError):
                    name_candidates |= extras
            # ---- ops history ------------------------------------------
            # The placeholders literal is built from the constant `?`
            # character — `name_candidates` only controls the COUNT,
            # never the placeholder STRING. Static analysers
            # (CodeQL, semgrep python.django.security.audit.sqli) flag
            # any f-string SQL builder regardless. The value is safely
            # parameterised below; we suppress with bandit's `# nosec`
            # marker so the audit trail makes the rationale explicit
            # at the call site instead of forcing a contrived rebuild
            # without f-strings (which would just make the SQL harder
            # to read).
            ph_count = max(1, len(name_candidates))
            placeholders = ",".join(["?"] * ph_count)
            hist_sql = (
                "SELECT ts, op_type, status, actor, target_name, target_id, "
                "target_stack, error, duration "
                "FROM history "
                "WHERE ts >= ? AND ("
                "  target_id IN (" + placeholders + ") "
                                                    "  OR target_name IN (" + placeholders + ")"
                                                                                             ") "
                                                                                             "ORDER BY ts DESC LIMIT ?"
            )  # nosec B608 — placeholders is a constant `?` string built from len(name_candidates); no taint flows from name_candidates into the SQL string itself.
            hist_rows = c.execute(
                hist_sql,
                (since, *name_candidates, *name_candidates, lim),
            ).fetchall()
            for r in hist_rows:
                status = (r["status"] or "").lower()
                severity = "success" if status == "success" else "error" if status == "error" else "info"
                op_type = r["op_type"] or "op"
                title_target = r["target_name"] or r["target_id"] or hid
                # Specialised event-kind for port_scan / port_scan_refresh
                # so the SPA's chip + icon helpers render them distinctly
                # instead of as generic "Op". Both surface as a single
                # `port_scan` kind on the timeline (the
                # `port_scan_refresh` aggregate row is per-tick / per-
                # schedule and isn't keyed by host_id, so it doesn't
                # appear here — only the per-host `port_scan` rows from
                # the shared scan helper do, regardless of whether the
                # scan was operator-initiated or schedule-fired).
                event_kind = "port_scan" if op_type == "port_scan" else "op"
                events.append({
                    "ts": int(r["ts"]),
                    "kind": event_kind,
                    "severity": severity,
                    "title": f"{op_type}",
                    "body": f"{title_target}" + (f" — {r['error']}" if r['error'] else ""),
                    "actor": r["actor"] or "system",
                    "metadata": {
                        "op_type": op_type,
                        "status": status,
                        "target_name": r["target_name"],
                        "target_id": r["target_id"],
                        "target_stack": r["target_stack"],
                        "duration": r["duration"],
                    },
                })
                counts["ops"] += 1

            # ---- notifications ----------------------------------------
            notif_rows = c.execute(
                "SELECT id, ts, event, severity, title, body, actor, "
                "target_kind, target_id, metadata "
                "FROM notifications "
                "WHERE ts >= ? AND target_kind = 'host' AND target_id = ? "
                "ORDER BY ts DESC LIMIT ?",
                (since, hid, lim),
            ).fetchall()
            for r in notif_rows:
                events.append({
                    "ts": int(r["ts"]),
                    "kind": "notification",
                    "severity": (r["severity"] or "info").lower(),
                    "title": r["title"] or r["event"] or "notification",
                    "body": r["body"] or "",
                    "actor": r["actor"] or "",
                    "metadata": {
                        "id": int(r["id"]),
                        "event": r["event"] or "",
                    },
                })
                counts["notifications"] += 1

            # ---- failure transition events ----------------------------
            # Pre-fix this synthesised `provider_paused` /
            # `provider_recovered` events from the CURRENT snapshot of
            # `host_failure_state` + `host_provider_last_ok`, which
            # meant a host that paused → resumed → paused → resumed
            # over the requested window only showed the LATEST state,
            # not the sequence. The new `host_failure_events`
            # append-only table captures every transition and replaces
            # both the failure-state and last-ok branches with one
            # ordered query. Schema-fallback path: if the table is
            # missing (e.g. a pre-migration deploy), we silently fall
            # back to the legacy current-state snapshot logic so the
            # timeline doesn't break during the rollout window.
            try:
                transition_rows = c.execute(
                    "SELECT ts, host_id, provider, kind, error, actor "
                    "FROM host_failure_events "
                    "WHERE host_id = ? AND ts >= ? "
                    "ORDER BY ts DESC LIMIT ?",
                    (hid, since, lim),
                ).fetchall()
            except sqlite3.OperationalError:
                # Pre-migration deploy where host_failure_events table
                # doesn't exist yet — fall back to empty + the legacy
                # current-state snapshot path below.
                transition_rows = []
            if transition_rows:
                for r in transition_rows:
                    kind_raw = (r["kind"] or "").lower()
                    provider = (r["provider"] or "").strip() or "host"
                    if kind_raw == "paused":
                        events.append({
                            "ts": int(r["ts"]),
                            "kind": "provider_paused",
                            "severity": "warning",
                            "title": f"{provider} sampling paused",
                            "body": (r["error"] or "auto-paused after consecutive failures")[:300],
                            "actor": r["actor"] or "sampler",
                            "metadata": {"provider": provider},
                        })
                        counts["failures"] += 1
                    elif kind_raw == "recovered":
                        events.append({
                            "ts": int(r["ts"]),
                            "kind": "provider_recovered",
                            "severity": "success",
                            "title": f"{provider} sampling recovered",
                            "body": "Probe succeeded — auto-pause cleared",
                            "actor": r["actor"] or "sampler",
                            "metadata": {"provider": provider},
                        })
                        counts["recoveries"] += 1
            else:
                # ---- legacy fallback: current-state snapshot only -----
                # Used when host_failure_events is empty (fresh install
                # before any transitions have been logged) OR when the
                # SELECT raised (table missing pre-migration). Keeps the
                # timeline functional during rollout; once the new table
                # has events the fallback never fires.
                try:
                    fail_rows = c.execute(
                        "SELECT provider, paused, paused_at, last_failure_ts, "
                        "last_error, consecutive_failures "
                        "FROM host_failure_state WHERE host_id = ?",
                        (hid,),
                    ).fetchall()
                except (sqlite3.Error, ValueError, TypeError):
                    fail_rows = []
                for r in fail_rows:
                    provider = (r["provider"] or "").strip() or "host"
                    if not r["paused"]:
                        continue
                    ts = int(r["paused_at"] or r["last_failure_ts"] or 0)
                    if ts < since:
                        continue
                    events.append({
                        "ts": ts,
                        "kind": "provider_paused",
                        "severity": "warning",
                        "title": f"{provider} sampling paused",
                        "body": (r["last_error"] or "auto-paused after consecutive failures")[:300],
                        "actor": "sampler",
                        "metadata": {
                            "provider": provider,
                            "consecutive_failures": int(r["consecutive_failures"] or 0),
                        },
                    })
                    counts["failures"] += 1
                try:
                    ok_rows = c.execute(
                        "SELECT provider, last_ok_ts "
                        "FROM host_provider_last_ok WHERE host_id = ?",
                        (hid,),
                    ).fetchall()
                except (sqlite3.Error, ValueError, TypeError):
                    ok_rows = []
                for r in ok_rows:
                    provider = (r["provider"] or "").strip() or "host"
                    ts = int(r["last_ok_ts"] or 0)
                    if ts < since:
                        continue
                    events.append({
                        "ts": ts,
                        "kind": "provider_recovered",
                        "severity": "success",
                        "title": f"{provider} probe ok",
                        "body": "Last successful probe",
                        "actor": "sampler",
                        "metadata": {"provider": provider},
                    })
                    counts["recoveries"] += 1
    except (sqlite3.Error, OSError, RuntimeError) as agg_err:
        print(f"[hosts] timeline {hid!r} aggregation error: {agg_err}")
        # Partial result — return what we have so far rather than 500.

    events.sort(key=lambda evt_row: evt_row["ts"], reverse=True)
    if len(events) > lim:
        events = events[:lim]

    return {
        "host_id": hid,
        "hours": h,
        "events": events,
        "counts": counts,
    }


# ---- Multi-host bulk-action endpoints --------------------------------
# Powers the Hosts main view's sticky bulk-action bar. Each endpoint
# accepts ``{host_ids: [...]}`` plus action-specific payload, validates
# every id against the curated ``hosts_config`` list, applies the
# change, persists the list, and returns
# ``{ok: bool, applied: [ids...], skipped: [ids...], errors: {id: msg}}``
# so the SPA can surface partial-success states.


class HostsBulkPauseIn(BaseModel):
    host_ids: list[str]


class HostsBulkResumeIn(BaseModel):
    host_ids: list[str]


class HostsBulkSnmpVendorsIn(BaseModel):
    host_ids: list[str]
    vendors: list[str]  # subset of _VALID_VENDOR_KEYS, [] = clear (auto-detect)
    mode: str = "set"  # "set" (replace) | "add" (union) | "remove" (difference)


class HostsBulkSnmpTunablesIn(BaseModel):
    host_ids: list[str]
    walk_concurrency: Optional[int] = None
    wall_clock_budget: Optional[int] = None
    clear: bool = False  # when true, REMOVES the per-host override (falls back to global tunable)


def _bulk_write_history_rows(
    host_ids: list[str], *,
    op_type: str, actor: str, started_ts: float,
) -> None:
    """Write one history audit row per host for a bulk action.

    Pre-fix the bulk-pause / bulk-resume endpoints left no audit trail
    — only the per-host endpoints wrote rows. Bulk callers therefore
    couldn't trace "who paused which 50 hosts at 03:14" through the
    Admin → History or per-host Timeline surfaces. This helper closes
    that gap by writing one row per matched host with a 'hosts' kind
    so the existing `target_kind` filter buckets them correctly.

    Best-effort — a per-row insert failure is logged and skipped so a
    single corrupt row never makes the bulk response 500. Same shape
    as the SCHEDULER_ACTOR / `_run_*` runners' history-row pattern.
    """
    if not host_ids:
        return
    _ops_mod.assert_op_type(op_type)
    try:
        with db_conn() as c:
            c.executemany(
                "INSERT INTO history "
                "(ts, op_type, target_kind, target_name, target_id, "
                " target_stack, status, duration, events, error, actor) "
                "VALUES (?, ?, 'hosts', ?, ?, NULL, 'success', 0.0, ?, NULL, ?)",
                [
                    (started_ts, op_type, hid, hid, "[]", actor)
                    for hid in host_ids
                ],
            )
    except Exception as e:  # noqa: BLE001
        print(f"[hosts:bulk] history write failed for {op_type}: {e}")


def _bulk_resolve_host_ids(host_ids: list[str], curated: list[dict]) -> tuple[list[str], list[str]]:
    """Return (matched_ids, missing_ids) by intersecting the requested
    set against the curated hosts_config index. Order preserved from
    the input list. De-duplicated."""
    by_id = {h.get("id"): h for h in curated}
    seen: set[str] = set()
    matched: list[str] = []
    missing: list[str] = []
    for raw in (host_ids or []):
        hid = (raw or "").strip()
        if not hid or hid in seen:
            continue
        seen.add(hid)
        if hid in by_id:
            matched.append(hid)
        else:
            missing.append(hid)
    return matched, missing


# ---------------------------------------------------------------------------
# Step-up reauth — single-flight short-lived tokens for bulk-destructive
# admin actions. Operator-stressed: a SweetAlert-only "are you sure?"
# is too easy to click through when the action affects N hosts at once.
# Higher-stakes endpoints (currently only `/api/hosts/bulk/pause`) take
# a `X-OmniGrid-Reauth-Token` header that the SPA mints by POSTing to
# `/api/admin/reauth` with the operator's local password just before
# the action. Tokens are single-use and TTL'd at 300s so a leaked
# header on a long-lived tab doesn't unlock arbitrary writes.
#
# Authentik / SSO users have no local password — the reauth endpoint
# returns a specific error code (`OG_REAUTH_NO_LOCAL_PASSWORD`) so the
# SPA can fall back to the existing typed-hostname / SweetAlert confirm
# without surfacing a misleading "wrong password" toast.
# ---------------------------------------------------------------------------
import secrets as _reauth_secrets  # noqa: E402

_REAUTH_TTL_SECONDS = 300

# Map of token → (user_id, expires_at). Single-replica safe (no
# horizontal scale), so an in-memory map is fine. Tokens are deleted on
# first successful use (single-use semantics).
_reauth_tokens: dict[str, tuple[int, float]] = {}


def _reauth_prune() -> None:
    """Drop expired tokens from the in-memory map. Called opportunistically
    on every mint + verify so the map can't grow unbounded. Single-pass
    over the map; cheap (typical N is single-digit per-process).
    """
    now = time.time()
    expired = [t for t, (_uid, exp) in _reauth_tokens.items() if exp <= now]
    for t in expired:
        _reauth_tokens.pop(t, None)


class ReauthIn(BaseModel):
    password: str


@app.post("/api/admin/reauth")
async def api_admin_reauth(
    body: ReauthIn,
    _request: Request,
    u: AdminUser,
):
    """Mint a short-lived reauth token for the calling admin.

    Verifies the supplied password against the user's stored bcrypt
    hash. SSO / Authentik users have no local password; the response
    code (`OG_REAUTH_NO_LOCAL_PASSWORD`) lets the SPA fall back to
    the typed-hostname confirm path on bulk pause.
    """
    _reauth_prune()
    pw = (body.password or "").strip()
    if not pw:
        raise HTTPException(400, detail="password required")
    # Pull the user's local password hash. Authentik / SSO users have
    # NULL or empty hashes — surface the distinct error so the SPA can
    # branch UI paths.
    try:
        with db_conn() as c:
            row = c.execute(
                "SELECT password_hash FROM users WHERE id = ?",
                (u.id,),
            ).fetchone()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, detail=f"reauth lookup failed: {e}") from e
    stored = (row and row["password_hash"]) or ""
    if not stored:
        return {
            "ok": False,
            "error_code": "OG_REAUTH_NO_LOCAL_PASSWORD",
            "detail": "Local password is not set for this account "
                      "(SSO user). Use the typed-hostname confirm path.",
        }
    if not auth.verify_password(pw, stored):
        # Audit the failure path — success is invisible by design (reauth
        # is a stepping stone). A per-event audit row surfaces who-tried-
        # when so the operator can spot brute-force-like patterns without
        # spelunking the per-IP login limiter's logs.
        try:
            with db_conn() as c:
                _ops_mod.write_admin_audit(
                    c, "admin_reauth_failed",
                    target_kind="auth", target_name=u.username, target_id=str(u.id),
                    actor=u.username, status="error", error="reauth failed",
                    message=f"admin reauth failed for {u.username}",
                )
        except Exception as e:
            print(f"[auth] admin_reauth_failed audit-row write failed: {e}")
        # Don't differentiate "wrong password" from "no user" — same
        # generic message reduces password-probing signal. The local-
        # auth login rate-limiter already covers brute force; we
        # don't double-rate-limit here because the reauth endpoint
        # requires an already-authenticated admin session.
        raise HTTPException(403, detail="reauth failed")
    token = _reauth_secrets.token_urlsafe(32)
    _reauth_tokens[token] = (int(u.id), time.time() + _REAUTH_TTL_SECONDS)
    return {
        "ok": True,
        "token": token,
        "expires_in": _REAUTH_TTL_SECONDS,
    }


def _user_has_local_password(user_id: int) -> bool:
    """Return True iff the user has a stored bcrypt password hash.

    SSO / Authentik users have NULL or empty hashes in the local
    `users` table (auth happens upstream via OIDC). For those users
    the reauth gate is meaningless — there's no password to verify
    against — so the dependency falls back to the typed-confirm path
    in the SPA without requiring a token.
    """
    try:
        with db_conn() as c:
            row = c.execute(
                "SELECT password_hash FROM users WHERE id = ?",
                (int(user_id),),
            ).fetchone()
    except (sqlite3.Error, ValueError, TypeError):
        return False
    return bool(row and (row["password_hash"] or "").strip())


def _require_reauth(request: Request, u: AdminUser) -> auth.User:
    """Dependency that consumes a `X-OmniGrid-Reauth-Token` header.

    Single-use: the token is deleted on first successful verify so a
    tab that leaks the header to JS console output can't replay.
    Bound to the originating user's id, so a token minted for admin A
    can't be replayed by admin B even if it leaked across sessions.

    SSO users (no local password hash) bypass the reauth check —
    there's no local password to verify against. The SPA's typed-
    hostname / typed-count confirm path is the sole gate for those
    callers; this dependency degrades gracefully so an Authentik
    admin doesn't get locked out of bulk operations.
    """
    if not _user_has_local_password(u.id):
        return u
    _reauth_prune()
    token = request.headers.get("X-OmniGrid-Reauth-Token", "").strip()
    if not token:
        raise HTTPException(
            401,
            detail="reauth required — POST /api/admin/reauth with your password",
        )
    pair = _reauth_tokens.get(token)
    if pair is None:
        raise HTTPException(401, detail="reauth token invalid or expired")
    user_id, expires_at = pair
    if time.time() >= expires_at:
        _reauth_tokens.pop(token, None)
        raise HTTPException(401, detail="reauth token expired")
    if int(user_id) != int(u.id):
        # Token doesn't match this caller — mismatch is a security
        # signal worth surfacing as a distinct 403 rather than the
        # generic 401, but only for debug. Operators don't routinely
        # see this without something genuinely off.
        raise HTTPException(403, detail="reauth token mismatch")
    # Single-use — burn the token now. A second attempt (legitimate
    # retry on an idempotent endpoint) needs a fresh reauth round-trip.
    _reauth_tokens.pop(token, None)
    return u


@app.post("/api/hosts/bulk/pause")
async def api_hosts_bulk_pause(
    body: HostsBulkPauseIn,
    request: Request,
    _u: auth.User = Depends(_require_reauth),
):
    """Mark every host in the request as auto-paused. Inserts/updates
    a row in ``host_failure_state`` with ``paused=1`` and
    ``paused_at=now`` so the lifespan-managed sampler short-circuits
    on the next tick. Idempotent — already-paused hosts return as
    ``applied`` so the bar's count badge stays consistent.
    """
    curated = _load_hosts_config()
    matched, missing = _bulk_resolve_host_ids(body.host_ids, curated)
    now = int(time.time())
    applied: list[str] = []
    errors: dict[str, str] = {}
    actor = _actor_from(request) or "admin"
    # Single transaction for the whole batch — pre-fix this opened
    # one db_conn() per host inside the loop. For 200 selected hosts
    # that was 200 SQLite write transactions; SQLite's WAL handles it
    # but the round-trip cost adds up. One outer connection +
    # `executemany` for the bulk INSERT-OR-UPDATE batches all writes
    # into a single transaction. Per-row failures (rare — schema-
    # constraint violations on a row whose hid was de-duped at boundary)
    # fall back to the per-row try/except path so partial-success
    # error reporting still works without losing a whole batch on one
    # bad row.
    pause_tag = f"manually paused by {actor}"
    if matched:
        try:
            with db_conn() as c:
                rows = [
                    (hid, float(now), pause_tag)
                    for hid in matched
                ]
                # ``first_failure_ts`` is NOT NULL on the schema. On the
                # INSERT path (host had no prior streak) we use the
                # SENTINEL ``0.0`` rather than ``now`` — a manual pause
                # is not a real failure event, so the
                # host_metrics_sampler's "is the failure window
                # expired?" math should not treat this row as a fresh
                # streak. ON CONFLICT path leaves ``first_failure_ts``
                # untouched so an EXISTING failure streak's start-time
                # isn't rewritten by a manual click.
                c.executemany(
                    "INSERT INTO host_failure_state "
                    "(host_id, provider, first_failure_ts, "
                    " consecutive_failures, paused, paused_at, last_error) "
                    "VALUES (?, '', 0.0, 0, 1, ?, ?) "
                    "ON CONFLICT(host_id, provider) DO UPDATE SET "
                    "paused = 1, paused_at = excluded.paused_at, "
                    "last_error = excluded.last_error",
                    rows,
                )
            applied = list(matched)
        except Exception as batch_err:  # noqa: BLE001
            # Batch failed (rare — likely DB-level error like disk
            # full). Fall back to per-row writes so partial success
            # is still possible + we get per-id error attribution.
            print(f"[hosts:bulk] pause batch failed, falling back to "
                  f"per-row: {batch_err}")
            for hid in matched:
                try:
                    with db_conn() as c:
                        c.execute(
                            "INSERT INTO host_failure_state "
                            "(host_id, provider, first_failure_ts, "
                            " consecutive_failures, paused, paused_at, last_error) "
                            "VALUES (?, '', 0.0, 0, 1, ?, ?) "
                            "ON CONFLICT(host_id, provider) DO UPDATE SET "
                            "paused = 1, paused_at = excluded.paused_at, "
                            "last_error = excluded.last_error",
                            (hid, float(now), pause_tag),
                        )
                    applied.append(hid)
                except Exception as e:  # noqa: BLE001
                    errors[hid] = str(e)
    # Per-host audit rows in `history` so Admin → History + the host
    # drawer's Timeline tab pick up bulk actions (pre-fix bulk pause/
    # resume left no audit trail — only the per-host endpoints did).
    # `target_kind='hosts'` matches the migration-#3 backfill rule for
    # `hosts_bulk_*` op_types. Best-effort; one bad row doesn't break
    # the response.
    if applied:
        _bulk_write_history_rows(
            applied, op_type="hosts_bulk_pause",
            actor=actor, started_ts=float(now),
        )
    # Publish ONE bulk SSE event so cross-tab observers reconcile N
    # rows from a single frame instead of N separate
    # `host:failure_state_changed` events. The SPA handler iterates
    # `host_ids` and triggers refreshHostRow per id (same effect,
    # single SSE write).
    try:
        client_id = _request_client_id(request)
        if applied:
            _events.publish(
                "host:bulk_action_applied",
                {
                    "action": "pause",
                    "host_ids": applied,
                    "actor": actor,
                },
                client_id=client_id,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[hosts:bulk] pause SSE publish failed: {e}")
    _full_host_cache_bust()
    print(f"[hosts:bulk] pause by {actor}: {len(applied)} applied, "
          f"{len(missing)} missing, {len(errors)} errors")
    return {
        "ok": not errors,
        "applied": applied,
        "skipped": missing,
        "errors": errors,
    }


@app.post("/api/hosts/bulk/resume")
async def api_hosts_bulk_resume(
    body: HostsBulkResumeIn,
    request: Request,
    _u: AdminUser,
):
    """Clear the auto-pause marker for every host in the request.
    Mirrors `/api/hosts/{host_id}/resume-sampling` per-row with the
    same cool-down clearing semantics, but skips the per-provider
    cool-down probes for speed — bulk callers that need full cool-down
    cleanup can fall back to the per-host endpoint.
    """
    curated = _load_hosts_config()
    matched, missing = _bulk_resolve_host_ids(body.host_ids, curated)
    applied: list[str] = []
    errors: dict[str, str] = {}
    actor = _actor_from(request) or "admin"
    # Single transaction for the whole batch — pre-fix this opened
    # one db_conn() per host inside the loop. For 200 selected hosts
    # that was 200 SQLite write transactions. After migration the
    # composite PK lets us DELETE every row (whole-host + every
    # per-provider variant) for a host in a single statement: the
    # IN list matches both ``host_id='hid' AND provider=''`` and
    # ``host_id='hid' AND provider='snmp'`` rows together. Per-row
    # failure (rare) falls back to per-host loop for partial success +
    # per-id error attribution.
    if matched:
        try:
            with db_conn() as c:
                placeholders = ",".join(["?"] * len(matched))
                c.execute(
                    "DELETE FROM host_failure_state WHERE host_id IN ("
                    + placeholders + ")",  # nosec B608 — placeholders is constant `?` literals
                    list(matched),
                )
            applied = list(matched)
        except Exception as batch_err:  # noqa: BLE001
            print(f"[hosts:bulk] resume batch failed, falling back "
                  f"to per-row: {batch_err}")
            for hid in matched:
                try:
                    with db_conn() as c:
                        c.execute(
                            "DELETE FROM host_failure_state WHERE host_id = ?",
                            (hid,),
                        )
                    applied.append(hid)
                except Exception as e:  # noqa: BLE001
                    errors[hid] = str(e)
    _full_host_cache_bust()
    # Per-host audit rows in `history` so Admin → History + the host
    # drawer's Timeline tab pick up bulk resumes (mirrors bulk-pause).
    if applied:
        _bulk_write_history_rows(
            applied, op_type="hosts_bulk_resume",
            actor=actor, started_ts=time.time(),
        )
    # ONE bulk SSE event covers every applied id — same contract as
    # the bulk-pause sister endpoint above. SPA's
    # `host:bulk_action_applied` handler iterates and refreshes each
    # row in place.
    try:
        client_id = _request_client_id(request)
        if applied:
            _events.publish(
                "host:bulk_action_applied",
                {
                    "action": "resume",
                    "host_ids": applied,
                    "actor": actor,
                },
                client_id=client_id,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[hosts:bulk] resume SSE publish failed: {e}")
    print(f"[hosts:bulk] resume by {actor}: {len(applied)} applied, "
          f"{len(missing)} missing, {len(errors)} errors")
    return {
        "ok": not errors,
        "applied": applied,
        "skipped": missing,
        "errors": errors,
    }


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.post("/api/hosts/bulk/snmp_vendors")
async def api_hosts_bulk_snmp_vendors(
    body: HostsBulkSnmpVendorsIn,
    request: Request,
    _u: AdminUser,
):
    """Apply an SNMP vendor MIB selection to every host in the request.

    ``mode``:
      * ``"set"`` (default) — replace each row's ``snmp.vendors`` with
        the supplied list. Empty list clears the override → resume
        auto-detect from sysDescr.
      * ``"add"`` — union the supplied vendors into each row's existing
        list. Useful for "also enable Cisco MIBs on these hosts" without
        clobbering existing per-host selections.
      * ``"remove"`` — difference. Drops each supplied vendor from the
        existing list; empty result removes the override (auto-detect).
    """
    curated = _load_hosts_config()
    matched, missing = _bulk_resolve_host_ids(body.host_ids, curated)
    cleaned_input = _clean_vendors_input(body.vendors) or set()
    mode = (body.mode or "set").lower()
    if mode not in ("set", "add", "remove"):
        raise HTTPException(400, f"Unsupported mode: {mode}")
    applied: list[str] = []
    errors: dict[str, str] = {}
    new_curated: list[dict] = []
    for h in curated:
        hid = h.get("id")
        if hid not in matched:
            new_curated.append(h)
            continue
        try:
            _raw_snmp_block = h.get("snmp")
            snmp_block: dict = _raw_snmp_block if isinstance(_raw_snmp_block, dict) else {}
            existing = set(snmp_block.get("vendors") or [])
            if mode == "set":
                next_vendors = set(cleaned_input)
            elif mode == "add":
                next_vendors = existing | cleaned_input
            else:  # remove
                next_vendors = existing - cleaned_input
            new_block = dict(snmp_block)
            if next_vendors:
                new_block["vendors"] = sorted(next_vendors)
            else:
                new_block.pop("vendors", None)
            new_h = dict(h)
            new_h["snmp"] = new_block
            new_curated.append(new_h)
            applied.append(hid)
        except Exception as e:
            errors[hid] = str(e)
            new_curated.append(h)
    if applied:
        try:
            _save_hosts_config(new_curated)
            _full_host_cache_bust()
        except HTTPException as e:
            return {
                "ok": False,
                "applied": [],
                "skipped": missing + applied,
                "errors": {"_save": e.detail},
            }
    actor = _actor_from(request) or "admin"
    # Bulk SSE event so cross-tab observers reload `hosts_config` +
    # refresh each affected row. Vendors edit curated config (NOT
    # failure state) so the SPA handler does a `loadHosts(true)` for
    # this action variant rather than per-row refresh.
    try:
        client_id = _request_client_id(request)
        if applied:
            _events.publish(
                "host:bulk_action_applied",
                {
                    "action": "snmp_vendors",
                    "host_ids": applied,
                    "actor": actor,
                    "mode": mode,
                    "vendors": sorted(cleaned_input),
                },
                client_id=client_id,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[hosts:bulk] snmp-vendors SSE publish failed: {e}")
    # Audit rows — one row per affected host so the History tab + per-host
    # Timeline both surface the change. Same shape as the pause/resume
    # bulk paths.
    _bulk_write_history_rows(
        applied,
        op_type="hosts_bulk_snmp_vendors",
        actor=actor,
        started_ts=time.time(),
    )
    print(f"[hosts:bulk] snmp-vendors by {actor} mode={mode} "
          f"vendors={sorted(cleaned_input)}: {len(applied)} applied, "
          f"{len(missing)} missing, {len(errors)} errors")
    return {
        "ok": not errors,
        "applied": applied,
        "skipped": missing,
        "errors": errors,
        "mode": mode,
        "vendors": sorted(cleaned_input),
    }


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.post("/api/hosts/bulk/snmp_tunables")
async def api_hosts_bulk_snmp_tunables(
    body: HostsBulkSnmpTunablesIn,
    request: Request,
    _u: AdminUser,
):
    """Apply per-host SNMP tunable overrides to every host in the request.

    Supported fields: ``walk_concurrency`` (1..16), ``wall_clock_budget``
    (5..600 seconds). Both optional — only fields present in the request
    are touched. ``clear=true`` REMOVES the override fields from each
    row's snmp block so the row falls back to the global tunable.
    """
    curated = _load_hosts_config()
    matched, missing = _bulk_resolve_host_ids(body.host_ids, curated)
    # Validate inputs against the same bounds _clean_host_snmp uses.
    wc: Optional[int] = None
    if body.walk_concurrency is not None and not body.clear:
        try:
            wc_val = int(body.walk_concurrency)
            if not (1 <= wc_val <= 16):
                raise HTTPException(400, "walk_concurrency must be in [1, 16]")
            wc = wc_val
        except (TypeError, ValueError):
            raise HTTPException(400, "walk_concurrency must be an integer")
    wcb: Optional[int] = None
    if body.wall_clock_budget is not None and not body.clear:
        try:
            wcb_val = int(body.wall_clock_budget)
            if not (5 <= wcb_val <= 600):
                raise HTTPException(400, "wall_clock_budget must be in [5, 600]")
            wcb = wcb_val
        except (TypeError, ValueError):
            raise HTTPException(400, "wall_clock_budget must be an integer")
    if not body.clear and wc is None and wcb is None:
        raise HTTPException(400, "supply walk_concurrency, wall_clock_budget, or clear=true")
    applied: list[str] = []
    errors: dict[str, str] = {}
    new_curated: list[dict] = []
    for h in curated:
        hid = h.get("id")
        if hid not in matched:
            new_curated.append(h)
            continue
        try:
            snmp_block = dict(h.get("snmp") or {}) if isinstance(h.get("snmp"), dict) else {}
            if body.clear:
                snmp_block.pop("walk_concurrency", None)
                snmp_block.pop("wall_clock_budget", None)
            else:
                if wc is not None:
                    snmp_block["walk_concurrency"] = wc
                if wcb is not None:
                    snmp_block["wall_clock_budget"] = wcb
            new_h = dict(h)
            new_h["snmp"] = snmp_block
            new_curated.append(new_h)
            applied.append(hid)
        except Exception as e:
            errors[hid] = str(e)
            new_curated.append(h)
    if applied:
        try:
            _save_hosts_config(new_curated)
            _full_host_cache_bust()
        except HTTPException as e:
            return {
                "ok": False,
                "applied": [],
                "skipped": missing + applied,
                "errors": {"_save": e.detail},
            }
    actor = _actor_from(request) or "admin"
    # Bulk SSE event — same shape as the snmp-vendors sister, edits
    # curated config not failure state.
    try:
        client_id = _request_client_id(request)
        if applied:
            _events.publish(
                "host:bulk_action_applied",
                {
                    "action": "snmp_tunables",
                    "host_ids": applied,
                    "actor": actor,
                    "clear": bool(body.clear),
                    "walk_concurrency": wc,
                    "wall_clock_budget": wcb,
                },
                client_id=client_id,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[hosts:bulk] snmp-tunables SSE publish failed: {e}")
    # Audit rows — one row per affected host; same shape as the
    # snmp-vendors sister + the pause/resume bulk paths.
    _bulk_write_history_rows(
        applied,
        op_type="hosts_bulk_snmp_tunables",
        actor=actor,
        started_ts=time.time(),
    )
    print(f"[hosts:bulk] snmp-tunables by {actor} "
          f"clear={body.clear} wc={wc} wcb={wcb}: "
          f"{len(applied)} applied, {len(missing)} missing, {len(errors)} errors")
    return {
        "ok": not errors,
        "applied": applied,
        "skipped": missing,
        "errors": errors,
        "walk_concurrency": wc,
        "wall_clock_budget": wcb,
        "clear": body.clear,
    }


class PingTestIn(BaseModel):
    host_id: str
    # Optional ad-hoc overrides — when blank, the test honours the
    # host's persisted ping config (or the global defaults). Used by
    # the Settings-tab "Test ping" button when the operator has typed
    # values that haven't been saved yet.
    port: Optional[int] = None
    transport: Optional[str] = None
    timeout_seconds: Optional[float] = None


class HttpProbeTestIn(BaseModel):
    """Body for the one-shot HTTP / TLS / DNS probe test endpoint.

    ``url`` is mandatory — every other field is optional and falls
    back to the tunable defaults / curated row's ``http_probe``
    config when blank. ``accepted_status_codes`` accepts CSV
    ("200,301,302") or a list.
    """
    url: str
    timeout: Optional[float] = None
    dns_timeout: Optional[float] = None
    content_match: Optional[str] = None
    accepted_status_codes: Optional[str] = None  # CSV or single code
    verify_tls: Optional[bool] = None


class PortScanIn(BaseModel):
    """Optional override knobs for a one-shot port scan. Empty body
    is fine — the endpoint resolves every value from the host's
    effective config (per-host override → global default → built-in
    fallback) when not supplied.
    """
    ports: Optional[str] = None  # TCP CSV/range syntax
    timeout_s: Optional[int] = None
    concurrency: Optional[int] = None
    banner_grab: Optional[bool] = None  # Stage 2 default-OFF
    # UDP companion (Stage 2). When `udp` is true the endpoint runs
    # the UDP scanner alongside TCP via asyncio.gather and merges
    # the results with a `protocol` annotation per port. `udp_ports`
    # is an optional CSV/range override for the UDP target list;
    # empty falls back to the global setting then to
    # `port_scanner_udp.DEFAULT_UDP_PORTS`.
    udp: Optional[bool] = None
    udp_ports: Optional[str] = None
    udp_timeout_s: Optional[int] = None
    udp_concurrency: Optional[int] = None


async def _run_port_scan_async(
    *,
    hid: str,
    target: str,
    ports_list: list,
    timeout_s: int,
    concurrency: int,
    banner_grab: bool,
    udp_enabled: bool,
    udp_ports_list: list,
    udp_timeout_s: int,
    udp_concurrency: int,
    snmp_community: str,
    max_seconds: int,
    scan_id: str,
    started: float,
    h: dict,
    actor: str,
    client_id: Optional[str] = None,
) -> None:
    """Run a port scan + persist results out-of-band from the request.

    Fire-and-forget task spawned by ``api_hosts_port_scan`` so the
    HTTP request returns immediately (HTTP 202) instead of blocking
    for the full scan duration. Wide port-range scans (the 11000-port
    cap) can run minutes; reverse proxies (NPM / openresty) typically
    cap at 60s ``proxy_read_timeout`` and would 504 the synchronous
    path. By kicking the scan off here, the request budget stays
    short and the scan continues independently.

    Errors are caught + logged — there's no caller to raise them
    back to. Persistence + the ``port_scan:completed`` SSE publish
    happen at the end so the SPA picks up results without polling.
    """
    from logic import port_scanner as _ps
    try:
        if udp_enabled:
            from logic import port_scanner_udp as _ps_udp
            tcp_scan, udp_scan = await asyncio.wait_for(
                asyncio.gather(
                    _ps.scan_host(
                        target,
                        ports_list,
                        timeout_s=float(timeout_s),
                        concurrency=int(concurrency),
                        banner_grab=bool(banner_grab),
                    ),
                    _ps_udp.udp_scan_host(
                        target,
                        udp_ports_list,
                        timeout_s=float(udp_timeout_s),
                        concurrency=int(udp_concurrency),
                        snmp_community=str(snmp_community),
                    ),
                ),
                timeout=float(max_seconds),
            )
            scan = tcp_scan
        else:
            scan = await asyncio.wait_for(
                _ps.scan_host(
                    target,
                    ports_list,
                    timeout_s=float(timeout_s),
                    concurrency=int(concurrency),
                    banner_grab=bool(banner_grab),
                ),
                timeout=float(max_seconds),
            )
            udp_scan = None
    except asyncio.TimeoutError:
        # TCP scan timed out at the wall-clock budget. UDP scan
        # may have completed already (UDP defaults are friendlier
        # — 19 ports × 3 s / 8 concurrency ≈ 9 s) and `_run_port_scan_async`
        # was about to merge results when the gather timed out.
        # Pre-fix the timeout branch returned immediately with
        # ZERO persistence — the host row's `last_port_scan_ts`
        # never updated, the drawer kept showing "Last scanned 7h
        # ago" indefinitely, and the partial UDP discovery was
        # discarded. Now we salvage what we have: run the UDP scan
        # SYNCHRONOUSLY (its own short budget capped it already)
        # and persist its open ports under a new scan_id so the
        # drawer at least surfaces the UDP-only findings AND the
        # timestamp updates so the user sees the scan attempt happened.
        print(
            f"[port_scan] failed host_id={hid!r} target={target!r} "
            f"reason=timeout (>{max_seconds}s budget) scan_id={scan_id}"
        )
        partial_udp_open: list[dict] = []
        if udp_enabled:
            try:
                from logic import port_scanner_udp as _ps_udp
                udp_partial = await asyncio.wait_for(
                    _ps_udp.udp_scan_host(
                        target,
                        udp_ports_list,
                        timeout_s=float(udp_timeout_s),
                        concurrency=int(udp_concurrency),
                        snmp_community=str(snmp_community),
                    ),
                    timeout=30.0,  # bounded recovery — never block long
                )
                partial_udp_open = _ps_udp.open_udp_ports_only(udp_partial)
                print(
                    f"[port_scan] timeout-salvage host_id={hid!r} "
                    f"udp_open={len(partial_udp_open)} scan_id={scan_id}"
                )
            except Exception as e:  # noqa: BLE001
                print(
                    f"[port_scan] timeout-salvage failed host_id={hid!r} "
                    f"reason={type(e).__name__}: {e} scan_id={scan_id}"
                )
        try:
            with db_conn() as c:
                # Carry forward the PREVIOUS scan's open ports under
                # the new scan_id BEFORE adding the recovered UDP
                # findings. Pre-fix the chip strip read the latest
                # scan_id and saw ONLY the recovered UDP rows — the
                # earlier TCP discovery (22 / 80 / 443 / etc.) silently
                # disappeared because the new scan replaced the old.
                # Now the new scan_id inherits every row from the
                # most-recent prior scan; the recovered UDP rows then
                # extend / dedupe over that baseline so a sticky
                # listener stays visible AND a freshly-found one
                # surfaces. `(port, protocol)` tuple deduping prevents
                # double-rows when the same UDP/161 was already in
                # the previous scan.
                prev_head = c.execute(
                    "SELECT scan_id FROM host_port_scans "
                    "WHERE host_id = ? AND scan_id != ? "
                    "GROUP BY scan_id ORDER BY MAX(ts) DESC LIMIT 1",
                    (hid, scan_id),
                ).fetchone()
                carried_keys: set[tuple[int, str]] = set()
                if prev_head and prev_head["scan_id"]:
                    prev_rows = c.execute(
                        "SELECT port, service_hint, banner_excerpt, protocol "
                        "FROM host_port_scans WHERE scan_id = ?",
                        (prev_head["scan_id"],),
                    ).fetchall()
                    for r in prev_rows:
                        proto = r["protocol"] or "tcp"
                        port_n = int(r["port"])
                        carried_keys.add((port_n, proto))
                        c.execute(
                            "INSERT INTO host_port_scans "
                            "(ts, host_id, scan_id, port, service_hint, "
                            " banner_excerpt, protocol) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                int(time.time()),
                                hid,
                                scan_id,
                                port_n,
                                r["service_hint"] or "",
                                r["banner_excerpt"] or "",
                                proto,
                            ),
                        )
                    print(
                        f"[port_scan] timeout-salvage carried-forward "
                        f"host_id={hid!r} from-scan={prev_head['scan_id']!r} "
                        f"rows={len(prev_rows)}"
                    )
                # Now add the recovered UDP findings, skipping ones
                # already present in the carried-forward set so the
                # row count stays clean.
                for entry in partial_udp_open:
                    port_n = int(entry.get("port") or 0)
                    if (port_n, "udp") in carried_keys:
                        continue
                    c.execute(
                        "INSERT INTO host_port_scans "
                        "(ts, host_id, scan_id, port, service_hint, "
                        " banner_excerpt, protocol) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            int(time.time()),
                            hid,
                            scan_id,
                            port_n,
                            entry.get("service_hint") or "",
                            entry.get("banner_excerpt") or "",
                            "udp",
                        ),
                    )
                _ops_mod.assert_op_type("port_scan")
                c.execute(
                    "INSERT INTO history "
                    "(ts, op_type, target_kind, target_name, target_id, "
                    " status, duration, events, error, actor) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        float(time.time()),
                        "port_scan",
                        "host",
                        hid,
                        hid,
                        "error",
                        float(max_seconds),
                        json.dumps({
                            "scan_id": scan_id,
                            "target": target,
                            "udp_open_partial": len(partial_udp_open),
                            "tcp_timeout": True,
                        }),
                        f"timeout (>{max_seconds}s budget) — TCP scan exceeded budget; UDP partial results persisted ({len(partial_udp_open)} open)",
                        actor,
                    ),
                )
                c.commit()
        except Exception as e:  # noqa: BLE001
            print(f"[port_scan] history-insert failed after timeout for {hid}: {e}")
        try:
            _events.publish("port_scan:completed", {
                "host_id": hid,
                "scan_id": scan_id,
                "ok": False,
                "target": target,
                "error": "timeout",
                "ports_open": 0,
                "udp_open": len(partial_udp_open),
            }, client_id=client_id)
        except (RuntimeError, OSError):
            pass
        return
    except Exception as e:  # noqa: BLE001
        print(
            f"[port_scan] failed host_id={hid!r} target={target!r} "
            f"reason={type(e).__name__}: {e} scan_id={scan_id}"
        )
        try:
            _events.publish("port_scan:completed", {
                "host_id": hid, "scan_id": scan_id, "ok": False,
                "target": target,
                "error": f"{type(e).__name__}: {e}",
            }, client_id=client_id)
        except (RuntimeError, OSError):
            pass
        return
    duration_ms = scan.get("duration_ms") or int((time.time() - started) * 1000)
    open_entries = _ps.open_ports_only(scan)
    for e in open_entries:
        e.setdefault("protocol", "tcp")
    udp_open_entries: list[dict] = []
    if udp_enabled and udp_scan is not None:
        from logic import port_scanner_udp as _ps_udp
        udp_open_entries = _ps_udp.open_udp_ports_only(udp_scan)
        udp_duration_ms = udp_scan.get("duration_ms") or 0
        if udp_scan.get("error"):
            print(
                f"[port_scan] udp failed host_id={hid!r} target={target!r} "
                f"reason={udp_scan.get('error')!r} scan_id={scan_id} "
                f"udp_duration_ms={udp_duration_ms}"
            )
        else:
            print(
                f"[port_scan] udp ok host_id={hid!r} target={target!r} "
                f"udp_ports_scanned={len(udp_scan.get('ports') or [])} "
                f"udp_ports_open={len(udp_open_entries)} "
                f"udp_duration_ms={udp_duration_ms} scan_id={scan_id}"
            )
    if scan.get("error"):
        print(
            f"[port_scan] failed host_id={hid!r} target={target!r} "
            f"reason={scan.get('error')!r} scan_id={scan_id} "
            f"duration_ms={duration_ms}"
        )
    else:
        print(
            f"[port_scan] ok host_id={hid!r} target={target!r} "
            f"ports_scanned={len(scan.get('ports') or [])} "
            f"ports_open={len(open_entries)} duration_ms={duration_ms} "
            f"scan_id={scan_id}"
        )
    prev_open_ports: set[tuple[int, str]] = set()
    is_first_scan = True  # default-true; flipped to False if a prior scan_id row exists
    try:
        with db_conn() as c:
            prev_head = c.execute(
                "SELECT scan_id, MAX(ts) AS ts FROM host_port_scans "
                "WHERE host_id = ? AND scan_id != ? "
                "GROUP BY scan_id ORDER BY ts DESC LIMIT 1",
                (hid, scan_id),
            ).fetchone()
            if prev_head and prev_head["scan_id"]:
                is_first_scan = False
                prev_rows = c.execute(
                    "SELECT port, protocol FROM host_port_scans WHERE scan_id = ?",
                    (prev_head["scan_id"],),
                ).fetchall()
                prev_open_ports = {
                    (int(r["port"]), (r["protocol"] or "tcp"))
                    for r in prev_rows
                }
    except (sqlite3.Error, ValueError, TypeError, KeyError):
        prev_open_ports = set()
    _raw_curated_services_for_diff = h.get("services")
    curated_services_for_diff: list = _raw_curated_services_for_diff if isinstance(_raw_curated_services_for_diff, list) else []
    curated_ports_set = {int(s.get("port") or 0)
                         for s in curated_services_for_diff if isinstance(s, dict)}
    try:
        with db_conn() as c:
            for entry in open_entries:
                c.execute(
                    "INSERT INTO host_port_scans "
                    "(ts, host_id, scan_id, port, service_hint, "
                    " banner_excerpt, protocol) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        int(scan.get("scanned_at") or time.time()),
                        hid,
                        scan_id,
                        int(entry.get("port") or 0),
                        entry.get("service_hint") or "",
                        entry.get("banner_excerpt") or "",
                        "tcp",
                    ),
                )
            for entry in udp_open_entries:
                c.execute(
                    "INSERT INTO host_port_scans "
                    "(ts, host_id, scan_id, port, service_hint, "
                    " banner_excerpt, protocol) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        int((udp_scan or {}).get("scanned_at") or time.time()),
                        hid,
                        scan_id,
                        int(entry.get("port") or 0),
                        entry.get("service_hint") or "",
                        entry.get("banner_excerpt") or "",
                        "udp",
                    ),
                )
            events_payload = {
                "scan_id": scan_id,
                "ports_scanned": len(scan.get("ports") or []),
                "ports_open": len(open_entries),
                "scan_duration_ms": duration_ms,
                "target": target,
                "udp_enabled": bool(udp_enabled),
                "udp_ports_scanned": len((udp_scan or {}).get("ports") or []) if udp_enabled else 0,
                "udp_ports_open": len(udp_open_entries) if udp_enabled else 0,
                "udp_scan_duration_ms": int((udp_scan or {}).get("duration_ms") or 0) if udp_enabled else 0,
            }
            try:
                events_json = json.dumps(events_payload, ensure_ascii=False)
            except (TypeError, ValueError):
                events_json = "{}"
            _ops_mod.assert_op_type("port_scan")
            c.execute(
                "INSERT INTO history "
                "(ts, op_type, target_kind, target_name, target_id, "
                " status, duration, events, error, actor) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    float(time.time()),
                    "port_scan",
                    "host",
                    hid,
                    hid,
                    "success" if not scan.get("error") else "error",
                    float(duration_ms) / 1000.0,
                    events_json,
                    scan.get("error") or None,
                    actor,
                ),
            )
            c.commit()
    except Exception as e:  # noqa: BLE001
        print(f"[port_scan] persist failed for {hid}: {e}")

    # Compute "new since last scan" — the SPA's completion toast
    # surfaces this so the operator sees at-a-glance whether the
    # current scan found anything different. Computed BEFORE the
    # notify-only `new_ports` filter below (which additionally
    # excludes curated ports for notification noise) so the toast
    # count reflects the raw "new this scan" tally regardless of
    # whether the new ports are curated.
    all_open = list(open_entries) + list(udp_open_entries)
    new_count_for_toast = 0
    if prev_open_ports and not scan.get("error"):
        new_count_for_toast = sum(
            1 for e in all_open
            if (int(e.get("port") or 0), (e.get("protocol") or "tcp")) not in prev_open_ports
        )
    if prev_open_ports and not scan.get("error"):
        new_ports = [
            e for e in all_open
            if (int(e.get("port") or 0), (e.get("protocol") or "tcp")) not in prev_open_ports
               and int(e.get("port") or 0) not in curated_ports_set
        ]
        if new_ports:
            try:
                from logic import ops as _ops
                for entry in new_ports:
                    pnum = int(entry.get("port") or 0)
                    hint = entry.get("service_hint") or ""
                    proto = (entry.get("protocol") or "tcp").lower()
                    label = f"{pnum}/{proto}" + (f" ({hint})" if hint else "")
                    await _ops.notify(
                        f"🆕 New open port on {target}: {label}",
                        (
                            f"{label} listening on {target} — not in the previous "
                            f"scan and not in this host's curated services. "
                            f"Promote to curated in the host drawer if expected."
                        ),
                        event="port_scan_new_port",
                        actor_username=actor,
                        target_kind="host",
                        target_id=hid,
                        metadata={
                            "port": pnum,
                            "protocol": proto,
                            "service_hint": hint,
                            "scan_id": scan_id,
                        },
                    )
            except Exception as e:  # noqa: BLE001
                print(f"[port_scan] notify failed for {hid}: {e}")

    # Notify any open SPA tabs that the scan completed so they can
    # refresh `host.detected_ports` without polling. The publisher
    # carries the scan summary so the SPA's handler can show a toast
    # without a follow-up GET.
    try:
        _events.publish("port_scan:completed", {
            "host_id": hid,
            "scan_id": scan_id,
            "ok": not bool(scan.get("error")),
            "target": target,
            # Wire-level IP the scanner's OS resolver returned for
            # `target` BEFORE the first probe fired. Surfaced so the
            # toast / history can show what was actually hit at the
            # network layer when the host_id is a friendly alias
            # (e.g. `opnsense` → `192.X.X.X` via container's
            # search-domain resolution). None when getaddrinfo failed
            # OR the scanner couldn't extract it; SPA falls back to
            # `target` then `host_id` in that case.
            "resolved_ip": scan.get("resolved_ip"),
            "ports_open": len(open_entries),
            "udp_open": len(udp_open_entries),
            "duration_ms": duration_ms,
            "error": scan.get("error") or None,
            # Count of (port, protocol) tuples present in this scan
            # but ABSENT from the previous scan's open-set. Drives
            # the "(N new since last scan)" parenthetical in the
            # completion toast. 0 when first scan OR when nothing
            # opened since the last run. Distinct from the
            # `port_scan_new_port` notify path's `new_ports` list
            # — that filter additionally excludes curated ports to
            # cut notification noise; the toast count reflects the
            # raw diff so the user sees exactly what the scan saw.
            "new_count": int(new_count_for_toast),
            # Lets the SPA pick a "first scan" vs "diff vs prior scan"
            # toast wording. True when this is the host's very first
            # scan (no prior scan_id rows in host_port_scans). Saves
            # the SPA from showing "(0 new since last scan)" when
            # there IS no last scan.
            "is_first_scan": bool(is_first_scan),
        }, client_id=client_id)
    except (RuntimeError, OSError):
        pass


@app.post("/api/hosts/{host_id}/port-scan")
async def api_hosts_port_scan(
    host_id: str,
    request: Request,
    body: Optional[PortScanIn] = None,
    *,
    _admin: AdminUser,
):
    """On-demand port scan for one curated host. Admin-only.

    The actual scan runs as a fire-and-forget asyncio task spawned
    from this handler — pre-fix the endpoint blocked for the FULL
    scan duration (up to ``tuning_port_scan_max_seconds`` = 120 s)
    and tripped reverse-proxy timeouts (NPM / openresty default
    ``proxy_read_timeout`` is typically 60 s) on wide port-range
    scans, surfacing as a raw 504 HTML page in the SPA's toast.
    Now: pre-validate + resolve target / config synchronously,
    spawn the scan, return ``{scan_id, status: 'queued'}`` (HTTP 202)
    immediately. The scan persists to ``host_port_scans`` + writes a
    history row + emits a ``port_scan:completed`` SSE event when
    done; SPA picks up the new ``detected_ports`` via the SSE
    handler (or its 30 s polling fallback).
    """
    hid = (host_id or "").strip()
    if not get_setting_bool(Settings.PORT_SCAN_ENABLED):
        print(f"[port_scan] skipped host_id={hid!r} — provider disabled (master toggle off)")
        raise HTTPException(
            status_code=400,
            detail="Port-scan provider is disabled. Enable it in "
                   "Admin → Providers → Port Scan first.",
        )
    if not hid:
        print("[port_scan] skipped — host_id required")
        raise HTTPException(400, "host_id required")
    curated = _load_hosts_config()
    h = next((x for x in curated if x.get("id") == hid), None)
    if h is None:
        print(f"[port_scan] skipped host_id={hid!r} — not found in curated list")
        raise HTTPException(404, f"Host not found: {hid}")
    _raw_ps_cfg = h.get("port_scan")
    ps_cfg: dict = _raw_ps_cfg if isinstance(_raw_ps_cfg, dict) else {}
    # Per-host enabled flag wins; otherwise inherit the master.
    if "enabled" in ps_cfg and not ps_cfg.get("enabled"):
        print(f"[port_scan] skipped host_id={hid!r} — per-host enable flag is False")
        raise HTTPException(
            status_code=400,
            detail=f"Port scan is disabled for host {hid}. "
                   f"Enable it in Admin → Hosts.",
        )
    # Resolve the scan target. Resolution chain (FIRST non-empty wins):
    # curated `address` field (dedicated, provider-independent) →
    # per-host `ping.host` override → `ssh.fqdn` → `ssh.host` → bare
    # host_id. The curated `url` field is DELIBERATELY excluded —
    # it carries the clickable web-UI link the operator wants to
    # surface on the host card. Probing that would target the public
    # service relay instead of the LAN host (wrong data + privacy).
    #
    # The `address` field is the canonical dedicated probe target.
    # User-flagged: provider fields (snmp_name / ping.host / ssh.host)
    # can all be DISABLED independently — relying on any of them as
    # the primary probe target leaves port-scan broken when a provider
    # is turned off. The `address` field is independent of any
    # provider and survives provider toggles. Operators set it in
    # Admin → Hosts. If left blank, the chain falls through to
    # provider-specific overrides then the bare host_id.
    _raw_ssh_cfg = h.get("ssh")
    ssh_cfg: dict = _raw_ssh_cfg if isinstance(_raw_ssh_cfg, dict) else {}
    _raw_ping_cfg = h.get("ping")
    ping_cfg: dict = _raw_ping_cfg if isinstance(_raw_ping_cfg, dict) else {}
    target = (
        (h.get("address") or "").strip()
        or (ping_cfg.get("host") or "").strip()
        or (ssh_cfg.get("fqdn") or "").strip()
        or (ssh_cfg.get("host") or "").strip()
        or hid
    )
    target = target.strip() or hid
    # Effective config: request body → per-host → global → built-in.
    # Narrow `body` to PortScanIn (drop None) so every `body.X` access
    # below doesn't trip "Member 'None' of 'PortScanIn | None'" lint
    # diagnostics. The if-branch reassignment is the only form the
    # type-checker reliably narrows from `T | None` to `T`.
    if body is None:
        body = PortScanIn()
    from logic import port_scanner as _ps
    ports_csv = (
        (body.ports or "").strip()
        or (ps_cfg.get("ports") or "").strip()
        or (get_setting(Settings.PORT_SCAN_DEFAULT_PORTS) or "").strip()
    )
    ports_list = _ps.parse_port_csv(ports_csv) if ports_csv else list(_ps.DEFAULT_PORTS)
    _timeout_raw = (
        body.timeout_s
        if body.timeout_s is not None else
        ps_cfg.get("timeout_s")
        if ps_cfg.get("timeout_s") is not None else
        tuning.tuning_int(Tunable.PORT_SCAN_DEFAULT_TIMEOUT_SECONDS)
    )
    timeout_s: int = int(_timeout_raw) if _timeout_raw is not None else 0
    _concurrency_raw = (
        body.concurrency
        if body.concurrency is not None else
        ps_cfg.get("concurrency")
        if ps_cfg.get("concurrency") is not None else
        tuning.tuning_int(Tunable.PORT_SCAN_DEFAULT_CONCURRENCY)
    )
    concurrency: int = int(_concurrency_raw) if _concurrency_raw is not None else 0
    # UDP companion (Stage 2). Operator-flagged 2026-05-10: TCP and UDP
    # share a single master toggle (`port_scan_enabled`) — there's no
    # separate `port_scan_udp_enabled` flag anymore. UDP runs alongside
    # TCP whenever port scanning is enabled. The legacy `body.udp=true`
    # per-call override is preserved as an explicit "skip UDP this call"
    # escape hatch (`body.udp=false` disables UDP for this scan only;
    # otherwise UDP defaults to ON when the master toggle is on).
    # Results merge into a single `host_port_scans` write with the
    # `protocol` column distinguishing the families.
    udp_enabled = bool(body.udp) if body.udp is not None else True
    udp_ports_list: list[int] = []
    udp_timeout_s = 0
    udp_concurrency = 0
    if udp_enabled:
        from logic import port_scanner_udp as _ps_udp
        udp_ports_csv = (
            (body.udp_ports or "").strip()
            or (ps_cfg.get("udp_ports") or "").strip()
            or (get_setting(Settings.PORT_SCAN_UDP_DEFAULT_PORTS) or "").strip()
        )
        udp_ports_list = (
            _ps.parse_port_csv(udp_ports_csv) if udp_ports_csv
            else list(_ps_udp.DEFAULT_UDP_PORTS)
        )
        _udp_timeout_raw = (
            body.udp_timeout_s
            if body.udp_timeout_s is not None else
            ps_cfg.get("udp_timeout_s")
            if ps_cfg.get("udp_timeout_s") is not None else
            tuning.tuning_int(Tunable.PORT_SCAN_UDP_DEFAULT_TIMEOUT_SECONDS)
        )
        udp_timeout_s = int(_udp_timeout_raw) if _udp_timeout_raw is not None else 0
        _udp_concurrency_raw = (
            body.udp_concurrency
            if body.udp_concurrency is not None else
            ps_cfg.get("udp_concurrency")
            if ps_cfg.get("udp_concurrency") is not None else
            tuning.tuning_int(Tunable.PORT_SCAN_UDP_DEFAULT_CONCURRENCY)
        )
        udp_concurrency = int(_udp_concurrency_raw) if _udp_concurrency_raw is not None else 0
    # Hard bound the scan duration. Outer wall-clock budget flows
    # through TUNABLES so the operator can raise it for large ranges
    # (the 11000-port range cap can reach 10-15 minutes on a slow link).
    # The endpoint NO LONGER blocks on the budget — it spawns the scan
    # as a fire-and-forget asyncio task and returns 202 immediately.
    scan_id = str(uuid.uuid4())
    started = time.time()
    max_seconds = tuning.tuning_int(Tunable.PORT_SCAN_MAX_SECONDS)
    _raw_snmp_cfg = h.get("snmp")
    snmp_cfg: dict = _raw_snmp_cfg if isinstance(_raw_snmp_cfg, dict) else {}
    snmp_community = (
        snmp_cfg.get("community")
        or get_setting(Settings.SNMP_DEFAULT_COMMUNITY)
        or "public"
    )
    print(
        f"[port_scan] queued host_id={hid!r} target={target!r} "
        f"ports={len(ports_list)} timeout_s={timeout_s} "
        f"concurrency={concurrency} banner_grab={bool(body.banner_grab)} "
        f"udp_enabled={udp_enabled} udp_ports={len(udp_ports_list)} "
        f"max_seconds={max_seconds} scan_id={scan_id}"
    )
    actor = getattr(_admin, "username", "ui") or "ui"
    spawn_background_task(
        _run_port_scan_async(
            hid=hid,
            target=target,
            ports_list=ports_list,
            timeout_s=int(timeout_s),
            concurrency=int(concurrency),
            banner_grab=bool(body.banner_grab),
            udp_enabled=bool(udp_enabled),
            udp_ports_list=udp_ports_list,
            udp_timeout_s=int(udp_timeout_s),
            udp_concurrency=int(udp_concurrency),
            snmp_community=str(snmp_community),
            max_seconds=int(max_seconds),
            scan_id=scan_id,
            started=started,
            h=h,
            actor=actor,
            client_id=_request_client_id(request),
        ),
        label=f"port_scan:{hid}:{scan_id[:8]}",
    )
    config_used = {
        "ports_count": len(ports_list),
        "timeout_s": int(timeout_s),
        "concurrency": int(concurrency),
        "banner_grab": bool(body.banner_grab),
        "udp_enabled": bool(udp_enabled),
    }
    if udp_enabled:
        config_used["udp_ports_count"] = len(udp_ports_list)
        config_used["udp_timeout_s"] = int(udp_timeout_s)
        config_used["udp_concurrency"] = int(udp_concurrency)
    return JSONResponse(
        status_code=202,
        content={
            "ok": True,
            "status": "queued",
            "host_id": hid,
            "target": target,
            "scan_id": scan_id,
            "scanned_at": int(started),
            "config_used": config_used,
        },
    )


@app.get("/api/history/port-scan/{scan_id}/ports")
async def api_history_port_scan_ports(
    scan_id: str,
    _admin: AdminUser,
):
    """Return the open ports recorded for a specific historical
    scan_id. Powers the History-tab detail popup for `op_type='port_scan'`
    rows so an operator clicking a past scan sees WHICH ports were
    open, not just the summary counts.

    Admin-only (the rest of the port-scan surface is admin-only too).
    """
    sid = (scan_id or "").strip()
    if not sid:
        raise HTTPException(400, "scan_id required")
    try:
        with db_conn() as c:
            rows = c.execute(
                "SELECT port, service_hint, banner_excerpt, ts, protocol "
                "FROM host_port_scans WHERE scan_id = ? "
                "ORDER BY protocol ASC, port ASC",
                (sid,),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"db read failed: {e}")
    return {
        "scan_id": sid,
        "ports": [
            {
                "port": int(r["port"]),
                "protocol": (r["protocol"] or "tcp"),
                "service_hint": r["service_hint"] or "",
                "banner_excerpt": r["banner_excerpt"] or "",
                "ts": int(r["ts"] or 0),
            }
            for r in rows
        ],
    }


@app.post("/api/ping/test")
async def api_ping_test(
    body: PingTestIn,
    _admin: AdminUser,
):
    """One-shot ping probe against a curated host. Used by the
    "Test ping" button in Settings → Host stats and the per-host test
    in Admin → Hosts. Always live (no cache); does NOT write to
    ``ping_samples`` so test-clicks don't pollute the chart series.
    """
    hid = (body.host_id or "").strip()
    if not hid:
        raise HTTPException(400, "host_id required")
    curated = _load_hosts_config()
    h = next((x for x in curated if x.get("id") == hid), None)
    if h is None:
        raise HTTPException(404, f"Host not found: {hid}")
    # Resolve the probe target the same way the sampler does so the
    # test result reflects what the sampler will actually probe. Chain:
    # `address → ping.host → ssh.fqdn → ssh.host → id`. Pre-fix this
    # used `ssh.fqdn → ssh.host → id` only — it skipped BOTH the
    # curated `address` field (the canonical dedicated probe target)
    # AND the per-host `ping.host` override, so a host that pinged
    # successfully via the live sampler reported "Test failed" here
    # because the test connect()ed to an unrelated `ssh.host` (or the
    # bare `id`, often unresolvable).
    _raw_ssh_cfg = h.get("ssh")
    ssh_cfg: dict = _raw_ssh_cfg if isinstance(_raw_ssh_cfg, dict) else {}
    _raw_pcfg_for_target = h.get("ping")
    pcfg_for_target: dict = _raw_pcfg_for_target if isinstance(_raw_pcfg_for_target, dict) else {}
    target = (
        (h.get("address") or "").strip()
        or (pcfg_for_target.get("host") or "").strip()
        or (ssh_cfg.get("fqdn") or "").strip()
        or (ssh_cfg.get("host") or "").strip()
        or hid
    )
    _raw_pcfg = h.get("ping")
    pcfg: dict = _raw_pcfg if isinstance(_raw_pcfg, dict) else {}
    default_port = tuning.tuning_int(Tunable.PING_DEFAULT_PORT) or 443
    port = body.port if body.port is not None else (pcfg.get("port") or default_port)
    use_icmp_global = get_setting_bool(Settings.PING_USE_ICMP)
    transport = (body.transport or pcfg.get("transport") or "").strip().lower()
    if transport not in ("tcp", "icmp"):
        transport = "icmp" if use_icmp_global else "tcp"
    timeout = float(body.timeout_seconds) if body.timeout_seconds is not None \
        else float(tuning.tuning_int(Tunable.PING_PROBE_TIMEOUT_SECONDS))
    from logic import ping as _ping_mod
    if transport == "icmp" and not _ping_mod.has_icmp_support():
        transport = "tcp"
    result = await _ping_mod.probe_ping(
        target, port=int(port), transport=transport,
        timeout_seconds=timeout,
    )
    return _stamp_test_success("ping", {
        "ok": bool(result.get("alive")),
        "host": target,
        "port": int(port),
        "transport": transport,
        **result,
    })


@app.post("/api/http-probe/test")
async def api_http_probe_test(
    body: HttpProbeTestIn,
    _admin: AdminUser,
):
    """One-shot HTTP / TLS / DNS probe against an arbitrary URL.

    Used by the "Test connection" button in the Admin → Host stats
    HTTP probe section + the per-host editor's row-level test.
    Always live — bypasses the persisted-cache lookup. Does NOT
    write to ``host_http_samples`` so test-clicks don't pollute the
    chart series. No history row (consistent with the other
    one-shot test endpoints).
    """
    from logic import http_probe as _http_probe
    url = (body.url or "").strip()
    if not url:
        raise HTTPException(400, "url required")
    timeout = float(body.timeout) if body.timeout is not None \
        else float(tuning.tuning_int(Tunable.HTTP_PROBE_TIMEOUT_SECONDS))
    dns_timeout = float(body.dns_timeout) if body.dns_timeout is not None \
        else float(tuning.tuning_int(Tunable.HTTP_PROBE_DNS_TIMEOUT_SECONDS))
    accepted = _http_probe.parse_status_codes_csv(body.accepted_status_codes)
    verify_tls = True if body.verify_tls is None else bool(body.verify_tls)
    content_match = (body.content_match or "").strip() or None
    result = await _http_probe.probe_http_health(
        url,
        timeout=timeout,
        dns_timeout=dns_timeout,
        content_match=content_match,
        accepted_status_codes=accepted,
        verify_tls=verify_tls,
    )
    return _stamp_test_success("http_probe", {
        "ok": bool(result.get("ok")),
        "url": url,
        **result,
    })


@app.get("/api/auth/providers")
async def api_auth_providers(request: Request):
    """Public endpoint: advertises which login paths are live. The login
    page queries this before rendering the SSO button so unconfigured
    deployments don't show a dead button that 503s.

    Multi-URL deployments: OIDC is reported `False` when the request's
    hostname doesn't match the configured `oidc_redirect_uri`'s host.
    OmniGrid is often reachable via multiple FQDNs (LAN /
    Cloudflare-tunnel / VPN), but Authentik will only honour the SSO
    flow for ONE registered redirect URI — opening the login page from
    any other URL would show a button that fails the round-trip with a
    "redirect_uri_mismatch" error. Hiding it on mismatched hostnames
    saves the operator a confusing trip into Authentik's logs.
    Hostname comparison is case-insensitive and ignores the port +
    path; an unparseable redirect URI falls back to "show the button"
    (defensive — better a useless button than hiding the SSO path on
    a config typo).
    """
    oidc_live = oidc.is_configured()
    if oidc_live:
        try:
            redirect_uri = (get_setting(Settings.OIDC_REDIRECT_URI) or "").strip()
            if redirect_uri:
                from urllib.parse import urlparse
                expected_host = (urlparse(redirect_uri).hostname or "").strip().lower()
                request_host = (request.url.hostname or "").strip().lower()
                # Both populated AND mismatched → hide the button.
                # Either side blank → fall through to "show" (don't lock
                # operators out on a misconfigured redirect URI).
                if expected_host and request_host and expected_host != request_host:
                    oidc_live = False
        except Exception as e:  # noqa: BLE001
            print(f"[auth] providers redirect_uri host-match check failed: {e}")
    return {
        "local": True,
        "oidc": oidc_live,
    }


@app.post("/api/notify-test")
async def api_notify_test(_admin: AdminUser):
    """Combined Test — fans out to EVERY enabled medium (app + apprise
    + telegram). Kept for back-compat with the legacy single-button UX;
    the Notifications admin tab now ALSO exposes per-channel Test
    buttons so operators can verify each channel independently."""
    await notify("🔔 OmniGrid test", "Notifications are wired up correctly!", "success")
    # Audit row — test-fires of real notifications (Apprise / app medium)
    # are side-effects on subscribers; the audit trail surfaces who-fired-
    # when so a noise complaint can be triaged back to the source.
    try:
        with db_conn() as c:
            _ops_mod.write_admin_audit(
                c, "notify_test",
                target_kind="notify", target_name="test",
                actor=_admin.username or "operator",
                message=f"test notification fired by {_admin.username or 'operator'}",
            )
    except Exception as e:
        print(f"[notify] notify_test audit-row write failed: {e}")
    return {"status": "sent"}


@app.post("/api/apprise/test")
async def api_apprise_test(_admin: AdminUser):
    """Per-channel Apprise Test — fires ONLY the Apprise medium.
    Per-channel siblings: `/api/telegram/test` (already exists). The
    combined `/api/notify-test` route stays for back-compat. Result
    shape matches the Telegram probe contract so the SPA can render
    the inline result chip identically across channels."""
    result = await _ops_mod.notify_medium_apprise(
        title="🔔 OmniGrid test",
        body="Apprise channel test — if you see this, the integration is wired correctly.",
        severity="success",
        event="apprise_test",
        actor_username=_admin.username,
        target_kind="notify", target_id="apprise_test",
        metadata=None,
    )
    try:
        with db_conn() as c:
            _ops_mod.write_admin_audit(
                c, "notify_test",
                target_kind="notify", target_name="apprise_test",
                actor=_admin.username or "operator",
                message=f"apprise channel test fired by {_admin.username or 'operator'}",
            )
    except Exception as e:
        print(f"[notify] apprise_test audit-row write failed: {e}")
    return _stamp_test_success("apprise", {
        "ok": bool(result.get("ok")),
        "detail": result.get("error") or result.get("skipped") or ("sent" if result.get("ok") else "failed"),
        "status": int(result.get("status") or 0),
    })


class _NotifySendIn(BaseModel):
    """Body for ``POST /api/notify/send`` — operator-driven custom
    message routed to ONE specific medium. Distinct from the per-medium
    Test endpoints (which fire a fixed payload). Backs the AI palette's
    ``send_notification`` action so the operator can say "send to
    telegram <text>" and have the AI dispatch it under their auth.
    """
    medium: str  # "app" | "apprise" | "telegram"
    body: str
    title: Optional[str] = None


@app.post("/api/notify/send")
async def api_notify_send(
    body_in: _NotifySendIn,
    _request: Request,
    _admin: AdminUser,
):
    """Send a custom (operator-typed) notification through ONE specific
    medium. Admin-only. The medium MUST be enabled in Admin →
    Notifications — disabled mediums short-circuit with a clear
    ``ok=False, detail=<reason>`` instead of silently dropping the
    message. Title defaults to ``"🔔 OmniGrid"`` when omitted so the
    AI palette's natural-language input doesn't have to invent one.

    Body length capped at 4096 chars (matches Telegram's per-message
    limit so the wire never rejects on size).

    Audit row written under ``op_type='notify_send'`` so the History
    tab surfaces every operator-driven send alongside the per-medium
    test fires.
    """
    medium = (body_in.medium or "").strip().lower()
    msg = (body_in.body or "").strip()
    title = (body_in.title or "").strip() or "🔔 OmniGrid"
    if not medium:
        raise HTTPException(400, "medium is required")
    if not msg:
        raise HTTPException(400, "body is required")
    if len(msg) > 4096:
        raise HTTPException(400, "body exceeds 4096 chars")
    if medium not in _ops_mod.NOTIFY_MEDIUMS:
        raise HTTPException(
            400,
            f"unknown medium '{medium}' — valid: "
            f"{', '.join(sorted(_ops_mod.NOTIFY_MEDIUMS.keys()))}",
        )
    actor = (_admin.username or "operator")
    result = await _ops_mod.notify_one_medium(
        medium=medium,
        title=title,
        body=msg,
        actor_username=actor,
        metadata={"source": "api_notify_send"},
    )
    # Audit row — same contract as the per-medium Test endpoints. Keeps
    # the History tab honest about who fired what, even when the message
    # is operator-typed rather than event-driven.
    try:
        _ops_mod.assert_op_type("notify_send")
        with db_conn() as c:
            _ops_mod.write_admin_audit(
                c, "notify_send",
                target_kind="notify", target_name=medium,
                actor=actor,
                message=(
                    f"custom notification fired by {actor} via {medium}: "
                    f"{msg[:140]}{'…' if len(msg) > 140 else ''}"
                ),
            )
    except Exception as e:  # noqa: BLE001
        print(f"[notify] notify_send audit-row write failed: {e}")
    return {
        "ok": bool(result.get("ok")),
        "medium": medium,
        "detail": result.get("detail") or result.get("error") or "",
    }


# ============================================================================
# In-app notifications store. Sibling of the Apprise medium —
# `logic.ops:notify` writes a row through the `app` medium on every
# enabled event AND publishes ``notification:created`` over SSE so the
# avatar badge + Notifications page update without polling. Routes are
# admin-only; bearer-token clients can poll on the same cookie/CSRF
# contract every other /api/ endpoint uses.
# ============================================================================
def _shape_notification_row(r) -> dict:
    """Cast a SQLite Row into the API JSON shape. Centralised so the
    list / SSE / mark-read paths all return the same field set.
    """
    md_raw = r["metadata"] if "metadata" in r.keys() else None
    md_obj: Optional[dict] = None
    if md_raw:
        try:
            md_obj = json.loads(str(md_raw))
        except (TypeError, ValueError):
            md_obj = None
    return {
        "id": int(r["id"]),
        "ts": int(r["ts"]),
        "event": r["event"] or "",
        "severity": r["severity"] or "info",
        "title": r["title"] or "",
        "body": r["body"] or "",
        "actor": r["actor"],
        "target_kind": r["target_kind"],
        "target_id": r["target_id"],
        "metadata": md_obj,
        "read_at": int(r["read_at"]) if r["read_at"] is not None else None,
    }


@app.get("/api/notifications")
async def api_notifications_list(
    limit: int = 50,
    offset: int = 0,
    unread_only: bool = False,
    event: Optional[str] = None,
    severity: Optional[str] = None,
    *,
    _admin: AdminUser,
):
    """Paginated list of in-app notifications, newest first.

    Filters compose with AND. ``limit`` is clamped to 1..200 (the SPA's
    default page size is 50; the upper cap keeps a bearer-token client
    from accidentally requesting the full table). Unread badge state is
    surfaced via ``unread_count`` regardless of the active filter so the
    SPA's avatar pill always reflects the global count.
    """
    try:
        limit_i = max(1, min(200, int(limit)))
    except (TypeError, ValueError):
        limit_i = 50
    try:
        offset_i = max(0, int(offset))
    except (TypeError, ValueError):
        offset_i = 0
    where_parts: list[str] = []
    params: list = []
    if unread_only:
        where_parts.append("read_at IS NULL")
    if event:
        where_parts.append("event = ?")
        params.append(str(event)[:100])
    if severity:
        sev = str(severity).strip().lower()
        if sev in ("info", "warning", "error", "success"):
            where_parts.append("severity = ?")
            params.append(sev)
    where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
    with db_conn() as c:
        rows = c.execute(
            "SELECT id, ts, event, severity, title, body, actor, "
            "target_kind, target_id, metadata, read_at "
            f"FROM notifications{where_sql} "
            "ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
            (*params, limit_i, offset_i),
        ).fetchall()
        total_row = c.execute(
            f"SELECT COUNT(*) AS n FROM notifications{where_sql}",
            tuple(params),
        ).fetchone()
        unread_row = c.execute(
            "SELECT COUNT(*) AS n FROM notifications WHERE read_at IS NULL"
        ).fetchone()
    return {
        "items": [_shape_notification_row(r) for r in rows],
        "total": int(total_row["n"]) if total_row else 0,
        "unread_count": int(unread_row["n"]) if unread_row else 0,
        "limit": limit_i,
        "offset": offset_i,
    }


@app.post("/api/notifications/{nid}/read")
async def api_notifications_mark_read(
    nid: int,
    request: Request,
    _admin: AdminUser,
):
    """Mark one notification row as read. Idempotent — already-read rows
    return 200 with the existing ``read_at``. 404 when the id doesn't
    exist so the SPA can prune ghost rows from a stale local cache.
    """
    now = int(time.time())
    with db_conn() as c:
        row = c.execute(
            "SELECT id, read_at FROM notifications WHERE id = ?", (nid,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="notification not found")
        if row["read_at"] is None:
            c.execute(
                "UPDATE notifications SET read_at = ? WHERE id = ?", (now, nid),
            )
            read_at = now
        else:
            read_at = int(row["read_at"])
        unread_row = c.execute(
            "SELECT COUNT(*) AS n FROM notifications WHERE read_at IS NULL"
        ).fetchone()
        unread_count = int(unread_row["n"]) if unread_row else 0
    # Push the new unread count over SSE so other tabs update their
    # badge without a round-trip. Self-filter via X-OmniGrid-Client-Id
    # so the originating tab doesn't echo-flicker its own click.
    try:
        _events.publish(
            "notification:read",
            {"id": nid, "read_at": read_at, "unread_count": unread_count},
            client_id=_request_client_id(request),
        )
    except Exception as _e:
        print(f"[notify] read SSE publish dropped: {_e}")
    return {"id": nid, "read_at": read_at, "unread_count": unread_count}


@app.post("/api/notifications/read-all")
async def api_notifications_mark_all_read(
    request: Request,
    _admin: AdminUser,
):
    """Mark every unread notification as read. Returns the count that
    was flipped so the SPA can show a "Marked N as read" toast and the
    badge zeros out atomically.
    """
    now = int(time.time())
    with db_conn() as c:
        cur = c.execute(
            "UPDATE notifications SET read_at = ? WHERE read_at IS NULL", (now,),
        )
        count = int(cur.rowcount or 0)
    try:
        _events.publish(
            "notification:read",
            {"id": None, "read_at": now, "unread_count": 0, "bulk": True},
            client_id=_request_client_id(request),
        )
    except Exception as _e:
        print(f"[notify] read-all SSE publish dropped: {_e}")
    return {"count": count, "unread_count": 0}


@app.delete("/api/notifications/{nid}")
async def api_notifications_delete(
    nid: int,
    request: Request,
    admin: AdminUser,
):
    """Admin-only delete one notification. Operators rarely need this —
    the prune_notifications schedule sweeps old rows automatically — but
    a one-off "scrub the test row" workflow is occasionally useful.
    """
    with db_conn() as c:
        cur = c.execute("DELETE FROM notifications WHERE id = ?", (nid,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="notification not found")
        unread_row = c.execute(
            "SELECT COUNT(*) AS n FROM notifications WHERE read_at IS NULL"
        ).fetchone()
        unread_count = int(unread_row["n"]) if unread_row else 0
        _ops_mod.write_admin_audit(
            c, "notification_delete",
            target_kind="notification", target_name=str(nid), target_id=str(nid),
            actor=admin.username,
            message=f"Deleted notification id={nid}",
        )
    try:
        _events.publish(
            "notification:deleted",
            {"id": nid, "unread_count": unread_count},
            client_id=_request_client_id(request),
        )
    except Exception as _e:
        print(f"[notify] delete SSE publish dropped: {_e}")
    return {"id": nid, "deleted": True, "unread_count": unread_count}


# ============================================================================
# Multi-tab activity registry — operators routinely run 3-5 OmniGrid tabs in
# parallel (one for stacks, one per debugging host, one for AI sidebar).
# Tracking each tab's current location lets the topbar widget show "you have
# 3 tabs open: Tab 2 = Stacks, Tab 3 = web01 drawer" + click-to-focus.
# Multi-tab activity tracking.
#
# Storage: in-process dict. Single-replica deploy = no need for SQLite. TTL
# expiry on stale heartbeats so closed-without-cleanup tabs (browser crash /
# kill -9) don't pile up forever. Read by the topbar widget; written by the
# SPA on every navigation event + on a 30s heartbeat tick.
# ============================================================================
_TAB_ACTIVITY_TTL_SECONDS = 90
# {client_id: {"actor", "view", "drawer_host", "admin_tab", "settings_section",
#              "stats_tab", "title", "ts"}}
_tab_activity_registry: dict[str, dict] = {}


def _tab_activity_prune() -> None:
    """Drop entries whose last heartbeat is older than the TTL. Called on
    every read so the live registry stays clean without a sweeper task."""
    cutoff = time.time() - _TAB_ACTIVITY_TTL_SECONDS
    stale: list[str] = []
    for cid, ent in _tab_activity_registry.items():
        _ts_raw = ent.get("ts") if isinstance(ent, dict) else None
        try:
            _ts_val = float(_ts_raw) if isinstance(_ts_raw, (int, float, str)) else 0.0
        except (TypeError, ValueError):
            _ts_val = 0.0
        if _ts_val < cutoff:
            stale.append(cid)
    for cid in stale:
        _tab_activity_registry.pop(cid, None)


def _parse_tab_activity_device(ua: str) -> dict:
    """Parse a User-Agent string into a compact device descriptor.

    Returns ``{form_factor, platform, browser, ua}`` where every field
    is a short tagged-string from a closed set so the SPA's popover can
    render an emoji + i18n-keyed label without further string juggling.

    Heuristic intentionally simple — UA parsing is messy but the popover
    only needs enough resolution to answer "is the other tab on a
    phone, a Mac, or a Windows laptop?" Field values:
      * ``form_factor`` ∈ {mobile, tablet, desktop}
      * ``platform``    ∈ {iOS, Android, Windows, Mac, Linux, BSD, ChromeOS, Other}
      * ``browser``     ∈ {Chrome, Firefox, Safari, Edge, Opera, Other}

    ``ua`` is the original string capped at 200 chars for the hover
    tooltip (two-laptop disambiguation). Empty input → every field
    ``Other`` / ``desktop`` so the parser never returns ``None``.
    """
    s = (ua or "").strip()[:512]
    low = s.lower()
    # Form-factor first — iPad must NOT match the iPhone branch (its UA
    # carries `Macintosh` on modern Safari for "Request Desktop Site"
    # behaviour, but still includes `iPad` when not toggled).
    if "ipad" in low or ("tablet" in low and "mobile" not in low):
        form_factor = "tablet"
    elif "iphone" in low or "ipod" in low or "mobi" in low or ("android" in low and "mobile" in low):
        form_factor = "mobile"
    else:
        form_factor = "desktop"
    # Platform — order matters because iOS UAs contain "Mac OS X"-ish
    # tokens and ChromeOS UAs contain "Linux".
    if "iphone" in low or "ipad" in low or "ipod" in low:
        platform = "iOS"
    elif "android" in low:
        platform = "Android"
    elif "cros" in low or "chromeos" in low:
        platform = "ChromeOS"
    elif "windows" in low:
        platform = "Windows"
    elif "mac os" in low or "macintosh" in low:
        platform = "Mac"
    elif "freebsd" in low or "openbsd" in low or "netbsd" in low:
        platform = "BSD"
    elif "linux" in low:
        platform = "Linux"
    else:
        platform = "Other"
    # Browser — Edge/Opera must precede Chrome because they ALSO carry
    # `Chrome/` in their UA. Safari last because every WebKit-based
    # browser carries `Safari/` in its UA.
    if "edg/" in low or "edge/" in low:
        browser = "Edge"
    elif "opr/" in low or "opera" in low:
        browser = "Opera"
    elif "firefox/" in low or "fxios" in low:
        browser = "Firefox"
    elif "chrome/" in low or "crios" in low:
        browser = "Chrome"
    elif "safari/" in low:
        browser = "Safari"
    else:
        browser = "Other"
    return {
        "form_factor": form_factor,
        "platform": platform,
        "browser": browser,
        "ua": s[:200],
    }


class _TabActivityIn(BaseModel):
    """Body for the heartbeat endpoint. Every field optional — the SPA
    sends only what's relevant to the current location. Rich-state
    fields (`drawer_item`, `filters`, `selection`, `rich_label`)
    power the "Reproduce here" handoff: a sibling tab's popover row
    can mirror the source tab's filter / drawer / sub-tab state into
    the current tab in one click. Empty / null = source tab was idle
    so the popover renders a one-line label."""
    view: Optional[str] = None
    drawer_host: Optional[str] = None
    drawer_item: Optional[str] = None
    admin_tab: Optional[str] = None
    settings_section: Optional[str] = None
    stats_tab: Optional[str] = None
    title: Optional[str] = None
    filters: Optional[dict] = None
    selection: Optional[list] = None
    rich_label: Optional[str] = None


@app.post("/api/tabs/activity")
async def api_tabs_activity_heartbeat(
    body: _TabActivityIn,
    request: Request,
):
    """Per-tab heartbeat. Updates the in-process registry + broadcasts a
    `tab:activity` SSE event so OTHER tabs see the location change in
    real time. Originating tab self-filters via the `client_id` echo
    (matches the existing event-bus self-filter pattern).

    Auth — relies on the global middleware's `/api/*` enforcement; no
    explicit dep needed. The middleware sets `request.state.user` when
    auth succeeds; we read the username off it for the `actor` field.
    """
    cid = _request_client_id(request)
    if not cid:
        return {"ok": False, "reason": "no client id"}
    actor = _actor_from(request)
    # Sanitise the rich-state payload BEFORE storing — filters dict
    # should only hold serialisable scalars (booleans, strings, short
    # arrays of strings) so a malicious or buggy SPA payload can't
    # blow the registry up. Selection cap at 50 ids matches the SPA-
    # side cap so wire + storage agree.
    # Explicit `dict` type (not Optional) so pyright can narrow the
    # in-loop writes. We emit None at the END when the caller passed
    # nothing — pre-fix the Optional[dict] declaration made every
    # `filters_clean[k] = v` raise a "Member 'None' of 'dict | None'"
    # warning even inside the isinstance-guarded block.
    filters_clean_dict: dict = {}
    has_filters = isinstance(body.filters, dict)
    if has_filters:
        for k, v in body.filters.items():  # type: ignore[union-attr]
            if not isinstance(k, str) or len(k) > 64:
                continue
            if isinstance(v, (bool, int, float)) or v is None:
                filters_clean_dict[k] = v
            elif isinstance(v, str) and len(v) <= 256:
                filters_clean_dict[k] = v
            elif isinstance(v, list) and len(v) <= 20:
                # CSV-shaped list of short strings (provider names etc.)
                filters_clean_dict[k] = [str(x)[:64] for x in v if isinstance(x, (str, int, float))]
    filters_clean: Optional[dict] = filters_clean_dict if has_filters else None
    selection_clean: Optional[list] = None
    if isinstance(body.selection, list):
        selection_clean = [str(x)[:128] for x in body.selection[:50] if isinstance(x, (str, int))]
    # Device descriptor from the request's User-Agent header — gives
    # operators a "which machine is the OTHER tab on" hint in the
    # popover (📱 iPhone · Safari / 🖥️ Mac · Firefox /...). Backend
    # parse so the SPA payload stays small AND so we don't depend on
    # client-hints API support (Safari lags). Hover-title carries the
    # raw UA (capped) for two-laptop disambiguation.
    device = _parse_tab_activity_device(request.headers.get("user-agent") or "")
    entry = {
        "actor": actor,
        "view": (body.view or "").strip() or None,
        "drawer_host": (body.drawer_host or "").strip() or None,
        "drawer_item": (body.drawer_item or "").strip() or None,
        "admin_tab": (body.admin_tab or "").strip() or None,
        "settings_section": (body.settings_section or "").strip() or None,
        "stats_tab": (body.stats_tab or "").strip() or None,
        "title": (body.title or "").strip() or None,
        "filters": filters_clean if filters_clean else None,
        "selection": selection_clean if selection_clean else None,
        "rich_label": (body.rich_label or "").strip() or None,
        "device": device,
        "ts": time.time(),
    }
    _tab_activity_registry[cid] = entry
    _tab_activity_prune()
    try:
        _events.publish(
            "tab:activity",
            {"client_id": cid, **entry},
            client_id=cid,  # self-filter: originating tab won't echo
        )
    except Exception as _e:
        print(f"[tabs] activity SSE publish dropped: {_e}")
    return {"ok": True}


@app.delete("/api/tabs/activity")
async def api_tabs_activity_close(request: Request):
    """Tab-close cleanup — fired from the SPA's `pagehide` event so
    other tabs see the entry vanish immediately instead of waiting for
    the 90s TTL."""
    cid = _request_client_id(request)
    if not cid:
        return {"ok": False, "reason": "no client id"}
    _tab_activity_registry.pop(cid, None)
    try:
        _events.publish(
            "tab:closed",
            {"client_id": cid},
            client_id=cid,
        )
    except Exception as _e:
        print(f"[tabs] close SSE publish dropped: {_e}")
    return {"ok": True}


@app.get("/api/tabs/activity")
async def api_tabs_activity_list(request: Request):
    """Snapshot of every active tab. Excludes the calling tab via
    `client_id` self-filter so the SPA's first-render doesn't display
    its own entry. Used at SPA boot to seed the local map before the
    SSE stream catches up."""
    cid = _request_client_id(request)
    _tab_activity_prune()
    out = []
    for tcid, ent in _tab_activity_registry.items():
        if cid and tcid == cid:
            continue
        out.append({"client_id": tcid, **ent})
    return {"tabs": out}


@app.get("/api/healthz")
async def healthz():
    """Liveness probe — returns 200 with the running version + uptime."""
    # Re-read VERSION.txt per request so operator edits on the server
    # (e.g. hand-bumping MAJOR/MINOR) show up without restarting the
    # container. File is tiny — a couple-microsecond stat+read each call.
    #
    # The container healthcheck only cares about HTTP 200 vs non-200, so
    # we intentionally keep returning 200 when config is broken — that
    # way Swarm doesn't crash-loop the task and the config-error page
    # stays reachable for the operator. The `ok` and `config_error`
    # fields let any JSON caller (Grafana, Uptime Kuma) distinguish
    # healthy from degraded.
    return {
        "ok": _db.DB_PATH_ERROR is None,
        "version": read_version(),
        "cache_age": int(time.time() - _cache["ts"]) if _cache["ts"] else None,
        "config_error": _db.DB_PATH_ERROR,
    }


@app.get("/api/version")
async def api_version():
    """Return the running OmniGrid version baked into the image at build time."""
    return {"version": read_version()}


# Admin → Version page was removed in 2026-04-30 alongside the deploy
# migration to image-build. Pre-fix the page wrote to /app/VERSION.txt
# via a per-file bind mount; post-fix the file is baked into the image
# at build time and any in-container write lands in the ephemeral
# overlay layer that the next `service update --force` discards. The
# durable seed path is now: edit repo-root VERSION.txt, commit, push —
# deploy.yml's source-B resolver (head -n1 ${DEPLOY_PATH}/VERSION.txt)
# picks it up as the floor for the next PATCH bump.


# ----------------------------------------------------------------------------
# Topbar weather widget — proxies an Open-Meteo-compatible instance so
# the browser dodges CORS and the same coordinate pair gets cached
# across tabs / reloads.
#
# URL is stored in the DB ``settings`` table under ``open_meteo_url``
# and is admin-authoritative (Admin → Notifications). There is NO
# hardcoded fallback — leaving the setting blank disables the weather
# endpoint entirely (returns ``{configured: false}``) so the operator
# isn't silently forwarded to api.open-meteo.com without opting in.
# ----------------------------------------------------------------------------
def _open_meteo_url() -> str:
    """Read the weather-upstream URL from settings.

    Returns the stored URL (trailing slash stripped) or the empty
    string when unset. Callers must treat `""` as "not configured"
    rather than falling back to a default.

    The per-service master switch `open_meteo_enabled` is
    consulted first — when disabled, return `""` regardless of what
    URL is stored. This way the URL stays in the settings table for
    when the operator flips back on, but the weather endpoint cleanly
    reports "not configured" while the switch is off.
    """
    from logic.db import get_setting_bool
    if not get_setting_bool(Settings.OPEN_METEO_ENABLED, default=True):
        return ""
    return (get_setting(Settings.OPEN_METEO_URL) or "").strip().rstrip("/")


_weather_cache: dict[tuple[float, float], tuple[float, dict]] = {}
_WEATHER_CACHE_TTL = 600.0  # 10 minutes — weather changes slowly

# WMO code → (short description, icon slug). Backend owns the mapping
# so i18n of condition strings has ONE source of truth.
_WMO_CODES: dict[int, tuple[str, str]] = {
    0: ("Clear", "sun"),
    1: ("Mainly clear", "sun"),
    2: ("Partly cloudy", "cloud-sun"),
    3: ("Cloudy", "cloud"),
    45: ("Fog", "fog"),
    48: ("Freezing fog", "fog"),
    51: ("Light drizzle", "drizzle"),
    53: ("Drizzle", "drizzle"),
    55: ("Heavy drizzle", "drizzle"),
    56: ("Freezing drizzle", "sleet"),
    57: ("Freezing drizzle", "sleet"),
    61: ("Light rain", "rain"),
    63: ("Rain", "rain"),
    65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "sleet"),
    67: ("Freezing rain", "sleet"),
    71: ("Light snow", "snow"),
    73: ("Snow", "snow"),
    75: ("Heavy snow", "snow"),
    77: ("Snow grains", "snow"),
    80: ("Rain showers", "rain"),
    81: ("Rain showers", "rain"),
    82: ("Heavy showers", "rain"),
    85: ("Snow showers", "snow"),
    86: ("Snow showers", "snow"),
    95: ("Thunderstorm", "thunder"),
    96: ("Thunder + hail", "thunder"),
    99: ("Thunder + hail", "thunder"),
}


@app.get("/api/public-ip")
async def api_public_ip(_admin: AdminUser):
    """Admin-only public-IP + ISP / ASN lookup. Standalone subsystem
    (NOT AI-related). The AI palette + Telegram /ip command both
    consume it but the feature owns its own Admin → Public IP section.

    Gated behind the `tuning_public_ip_enabled` tunable (default OFF
    for privacy — fetching reveals the deploy is reaching ifconfig.co).
    The helper in `logic.public_ip` handles the cache + the gate
    short-circuit; this endpoint just surfaces the result so callers
    can fold it into their context blocks.

    Returns `{enabled: false}` when the gate is off so the SPA knows
    to omit the prompt block and the AI doesn't try to answer "what's
    my public IP" from a refused/empty payload. On a soft fetch
    failure (transient network blip) returns `{enabled: true, ip: null,
    error: <detail>}` so the SPA can render a hint rather than
    silently swallowing.
    """
    from logic import public_ip as _public_ip
    if not _public_ip.is_enabled():
        return {"enabled": False}
    data = await _public_ip.fetch()
    if data is None:
        return {"enabled": True, "error": "lookup failed — see Admin → Logs"}
    return {"enabled": True, **data}


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.get("/api/weather")
async def api_weather(
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    label: str = "",
):
    """Fetch current conditions from Open-Meteo for one lat/lon.

    Caller persists label + coords in localStorage; this endpoint is
    stateless apart from an in-memory 10-min cache keyed by (lat, lon).
    Network errors degrade to ``{configured, error}`` so the topbar
    never breaks when the upstream is unreachable.
    """
    if lat is None or lon is None:
        return {"configured": False}
    upstream = _open_meteo_url()
    if not upstream:
        # Admin → General stores `open_meteo_url` (post-fix split out
        # of the legacy Notifications panel); blank disables the
        # widget entirely rather than forwarding to a hardcoded public
        # endpoint the operator didn't opt into.
        return {
            "configured": False,
            "error": "open_meteo_url not configured",
            "label": label,
        }
    # Quantise to 2 decimals so minor coord differences for the same
    # city hit one cache entry.
    key = (round(float(lat), 2), round(float(lon), 2))
    now = time.time()
    cached = _weather_cache.get(key)
    if cached and (now - cached[0]) < _WEATHER_CACHE_TTL:
        body = dict(cached[1])
        body["label"] = label or body.get("label") or ""
        body["cached"] = True
        return body

    params = {
        "latitude": str(key[0]),
        "longitude": str(key[1]),
        "current": "temperature_2m,weather_code,relative_humidity_2m,wind_speed_10m",
        # Daily forecast — covers the next 7 days. AI sidebar consumers
        # use this for "weather forecast next 5 days" questions; the
        # topbar widget keeps showing current-only and ignores the
        # forecast payload (small enough to ride the same response).
        "daily": (
            "temperature_2m_max,temperature_2m_min,weather_code,"
            "precipitation_sum,sunrise,sunset"
        ),
        "forecast_days": "7",
        "timezone": "auto",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(upstream, params=params)
            r.raise_for_status()
            j = r.json() or {}
    except Exception as e:
        return {"configured": True, "error": str(e), "label": label}

    cur = j.get("current") or {}
    code = int(cur.get("weather_code") or 0)
    desc, icon = _WMO_CODES.get(code, ("Unknown", "cloud"))
    # Build the daily forecast list — one entry per day with min/max
    # temp + weather code + precipitation sum. Empty list when the
    # upstream didn't return a `daily` block (degrades cleanly).
    forecast: list[dict] = []
    _raw_daily = j.get("daily")
    daily: dict = _raw_daily if isinstance(_raw_daily, dict) else {}
    times = daily.get("time") or []
    tmaxes = daily.get("temperature_2m_max") or []
    tmines = daily.get("temperature_2m_min") or []
    dcodes = daily.get("weather_code") or []
    precips = daily.get("precipitation_sum") or []
    # Sunrise / sunset surface the day's daylight window — Open-Meteo
    # returns ISO timestamps in the resolved IANA timezone (we pass
    # `timezone=auto`). Consumed by `/weather` for "should I go for a
    # run" / "is it light out" practical questions.
    sunrises = daily.get("sunrise") or []
    sunsets = daily.get("sunset") or []
    for i in range(min(len(times), 7)):
        try:
            d_code = int(dcodes[i]) if i < len(dcodes) else 0
        except (TypeError, ValueError):
            d_code = 0
        d_desc, _d_icon = _WMO_CODES.get(d_code, ("Unknown", "cloud"))
        forecast.append({
            "date": times[i],
            "temp_max_c": tmaxes[i] if i < len(tmaxes) else None,
            "temp_min_c": tmines[i] if i < len(tmines) else None,
            "code": d_code,
            "condition": d_desc,
            "precip_mm": precips[i] if i < len(precips) else None,
            "sunrise": sunrises[i] if i < len(sunrises) else None,
            "sunset": sunsets[i] if i < len(sunsets) else None,
        })
    body = {
        "configured": True,
        "label": label,
        "temp_c": cur.get("temperature_2m"),
        "humidity": cur.get("relative_humidity_2m"),
        "wind_kmh": cur.get("wind_speed_10m"),
        "code": code,
        "condition": desc,
        "icon": icon,
        "forecast": forecast,
        "provider": "open-meteo",
        "upstream": upstream,
        "fetched_at": int(now),
        # Open-Meteo returns the resolved IANA timezone when called
        # with `timezone=auto` — surface it so per-user `/time` in
        # the Telegram bot (and any future UI clock) can render local
        # time at the user's saved weather location.
        "timezone": j.get("timezone") or "",
        "timezone_abbrev": j.get("timezone_abbreviation") or "",
        "utc_offset_seconds": j.get("utc_offset_seconds") or 0,
    }
    _weather_cache[key] = (now, body)
    return body


# ============================================================================
# App logs — in-memory ring buffer of recent stdout/stderr lines.
# Admin-only. Frontend polls /api/logs?since=<ts> to incrementally
# fetch new lines; DELETE clears the buffer (does not affect Docker logs).
# Buffer lives in logic/logs.py; the tee is installed at module-import
# time so uvicorn's own lines are captured too.
# ============================================================================
@app.get("/api/logs")
async def api_logs(
    limit: int = 500,
    since: float = 0.0,
    *,
    _admin: AdminUser,
):
    """Return recent persistent-log lines filtered by severity / tag prefix."""
    # Clamp limit to a sane upper bound so a misconfigured client can't
    # pull the whole buffer repeatedly at poll rate.
    limit = max(1, min(int(limit), _logs.MAX_LINES))
    return {
        "logs": _logs.get_recent(limit=limit, since_ts=float(since)),
        "size": _logs.size(),
        "max": _logs.MAX_LINES,
    }


@app.delete("/api/logs")
async def api_logs_clear(_admin: AdminUser):
    """Truncate the in-memory log buffer (audit row written first)."""
    # Audit row BEFORE the clear so the forensic anchor survives even
    # the very destruction it records. Same pattern as DELETE /api/history.
    try:
        with db_conn() as c:
            _ops_mod.write_admin_audit(
                c, "logs_clear",
                target_kind="logs", target_name="in-memory",
                actor=_admin.username or "operator",
                message=f"in-memory log buffer cleared by {_admin.username or 'operator'}",
            )
    except Exception as e:
        print(f"[logs] audit-row write failed before clear: {e}")
    _logs.clear()
    return {"ok": True}


# ----------------------------------------------------------------------------
# Persistent log files. Daily files under /app/data/logs/.
# Admin-only. Three routes:
# GET /api/admin/logs/files                      — directory listing
# GET /api/admin/logs/files/{name}?tail=N        — text body, last N lines (N optional)
# GET /api/admin/logs/files/{name}/download      — full file as attachment
# Filename is validated against the canonical regex inside `safe_log_path`
# so path-traversal attempts (../, absolute paths) bounce with 404.
# ----------------------------------------------------------------------------
@app.get("/api/admin/logs/files")
async def api_admin_logs_files(_admin: AdminUser):
    """List the persistent log files on disk + the log directory."""
    return {"files": _logs.list_persistent_logs(), "log_dir": _logs.LOG_DIR}


@app.get("/api/admin/logs/files/{name}")
async def api_admin_logs_file_view(
    name: str,
    tail: int = 0,
    *,
    _admin: AdminUser,
):
    """Read one persistent-log file by name (path-traversal guarded)."""
    body = _logs.read_persistent_log(name, tail_lines=tail if tail > 0 else None)
    if body is None:
        return JSONResponse(status_code=404, content={"detail": "log file not found"})
    return Response(content=body, media_type="text/plain; charset=utf-8")


@app.get("/api/admin/logs/files/{name}/download")
async def api_admin_logs_file_download(
    name: str,
    _admin: AdminUser,
):
    """Stream one persistent log file. Name must be `<YYYY-MM-DD>.log` per
    `_logs.safe_log_path`'s validator; anything else 404s."""
    path = _logs.safe_log_path(name)
    if not path or not os.path.isfile(path):  # type: ignore[attr-defined]
        return JSONResponse(status_code=404, content={"detail": "log file not found"})
    return FileResponse(path, filename=name, media_type="text/plain; charset=utf-8")


# ============================================================================
# Auth routes (step 1: local login, logout, one-shot bootstrap, /api/me).
# Registered here — above the StaticFiles catch-all — per CLAUDE.md.
# ============================================================================
# ----------------------------------------------------------------------------
# TOTP / 2FA challenge store. In-memory dict mapping
# challenge_id -> {user_id, kind, secret?, issued_at, expires_at}. Lifespan-
# scoped because the matching cookie isn't issued until the second step
# completes. Single-replica pinning (CLAUDE.md) makes this safe.
# ``kind`` is one of:
# "totp_required"      — user has TOTP enrolled; verifying a code
# "totp_setup_required" — policy forces enrolment; user must set up
#                          TOTP before the cookie is issued.
# ----------------------------------------------------------------------------
_TOTP_CHALLENGE_TTL_SECONDS = 5 * 60
_totp_challenges: dict[str, dict] = {}


def _prune_totp_challenges() -> None:
    now = time.time()
    stale: list[str] = []
    for k, v in _totp_challenges.items():
        _exp_raw = v.get("expires_at", 0) if isinstance(v, dict) else 0
        try:
            _exp_val = float(_exp_raw) if isinstance(_exp_raw, (int, float, str)) else 0.0
        except (TypeError, ValueError):
            _exp_val = 0.0
        if _exp_val <= now:
            stale.append(k)
    for k in stale:
        _totp_challenges.pop(k, None)


def _create_totp_challenge(payload: dict) -> tuple[str, int]:
    _prune_totp_challenges()
    cid = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + _TOTP_CHALLENGE_TTL_SECONDS
    _totp_challenges[cid] = {**payload, "expires_at": expires_at}
    return cid, expires_at


def _consume_totp_challenge(cid: str) -> Optional[dict]:
    _prune_totp_challenges()
    return _totp_challenges.pop(cid, None)


def _peek_totp_challenge(cid: str) -> Optional[dict]:
    _prune_totp_challenges()
    return _totp_challenges.get(cid)


# ----------------------------------------------------------------------------
# WebAuthn (passkey) challenge stores. Two flavours, both the same
# in-memory dict shape as the TOTP store -- single-replica deploy makes it
# safe. Pruned lazily on every read/write.
#
# _webauthn_login_challenges -- raw challenge bytes pending second-
#     factor verification. Keyed by challenge_id (opaque token the
#     SPA echoes back). Created by /api/local-auth/webauthn-start;
#     consumed by /api/local-auth/webauthn-finish. 5-min TTL.
#
# _webauthn_register_challenges -- raw challenge bytes pending
#     enrolment. Keyed by user_id (the call sites are authed and we
#     only allow one in-flight enrolment per user). Created by
#     /api/me/webauthn/register-start; consumed by register-finish.
#     5-min TTL.
#
# RP ID + origin are derived per-request from the URL the SPA hit
# (request.url.hostname / .scheme), so dev (localhost:8088) and prod
# (NPM-fronted domain) both work without a settings entry.
# ----------------------------------------------------------------------------
_WEBAUTHN_CHALLENGE_TTL_SECONDS = 5 * 60
_webauthn_login_challenges: dict[str, dict] = {}
_webauthn_register_challenges: dict[int, dict] = {}


# noinspection PyTypeChecker,PyUnresolvedReferences
def _prune_webauthn_challenges() -> None:
    now = time.time()
    for k in [k for k, v in _webauthn_login_challenges.items()
              if float(v.get("expires_at", 0)) <= now]:
        _webauthn_login_challenges.pop(k, None)
    for k in [k for k, v in _webauthn_register_challenges.items()
              if float(v.get("expires_at", 0)) <= now]:
        _webauthn_register_challenges.pop(k, None)


def _create_webauthn_login_challenge(payload: dict) -> tuple[str, int]:
    _prune_webauthn_challenges()
    cid = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + _WEBAUTHN_CHALLENGE_TTL_SECONDS
    _webauthn_login_challenges[cid] = {**payload, "expires_at": expires_at}
    return cid, expires_at


def _consume_webauthn_login_challenge(cid: str) -> Optional[dict]:
    _prune_webauthn_challenges()
    return _webauthn_login_challenges.pop(cid, None)


def _peek_webauthn_login_challenge(cid: str) -> Optional[dict]:
    _prune_webauthn_challenges()
    return _webauthn_login_challenges.get(cid)


def _set_webauthn_register_challenge(user_id: int, payload: dict) -> int:
    _prune_webauthn_challenges()
    expires_at = int(time.time()) + _WEBAUTHN_CHALLENGE_TTL_SECONDS
    _webauthn_register_challenges[user_id] = {
        **payload, "expires_at": expires_at,
    }
    return expires_at


def _consume_webauthn_register_challenge(user_id: int) -> Optional[dict]:
    _prune_webauthn_challenges()
    return _webauthn_register_challenges.pop(user_id, None)


def _request_rp_id(request: Request) -> str:
    """Derive the WebAuthn RP ID from the incoming request.

    RP ID is the hostname (no port, no scheme) the SPA hit, AS THE
    BROWSER SEES IT — has to be a registrable suffix of the page's
    actual origin or `navigator.credentials.create()` rejects with
    SecurityError. Behind a reverse proxy (NPM in OmniGrid's deploy)
    the upstream connection's URL has the internal hostname (typically
    ``localhost`` or the Docker stack name), which would mismatch the
    public domain the browser sees and break enrolment.

    Resolution order: ``X-Forwarded-Host`` header (what proxies set
    when they want the backend to know the original Host), then the
    ``Host`` header (NPM forwards this verbatim), then
    ``request.url.hostname`` as a last resort for direct (non-proxied)
    dev runs. Strip the ``:port`` suffix in every case — RP IDs are
    hostname-only.

    the WebAuthn register-finish path calls this
    twice (directly + via `_request_origin`); cache the resolved value
    on `request.state.rp_id` so the second call is a dict lookup.
    """
    cached = getattr(request.state, "rp_id", None)
    if isinstance(cached, str):
        return cached
    candidates = [
        request.headers.get("x-forwarded-host", ""),
        request.headers.get("host", ""),
        request.url.hostname or "",
    ]
    for raw in candidates:
        host = (raw or "").split(",")[0].strip().lower()
        if ":" in host:
            host = host.split(":", 1)[0]
        if host:
            try:
                request.state.rp_id = host
            except (AttributeError, RuntimeError):
                # `WebSocket` doesn't expose `state` like Request — the
                # cache is best-effort; just skip when unavailable.
                pass
            return host
    raise HTTPException(
        status_code=400,
        detail=_err.message_for(_err.AUTH_WEBAUTHN_RP_ID_UNRESOLVABLE),
    )


def _request_origin(request) -> str:
    """Full origin used for WebAuthn assertion verification AND for the
    WebSocket admin-route Origin gate.

    Accepts either a Starlette ``Request`` or a ``WebSocket``; both
    expose ``.headers`` and ``.url`` with the shape we need so the
    helper duck-types cleanly.

    Resolution order matches ``_request_rp_id`` — ``X-Forwarded-Host``
    (what the public-facing reverse proxy sets to convey the original
    Host), then the ``Host`` header, then ``request.url.netloc /
    .hostname`` as a final fallback. Some NPM setups rewrite the Host
    header to the internal upstream hostname while preserving the
    public hostname in X-Forwarded-Host — if origin disagrees with
    rp_id, the WebAuthn verifier rejects with "Unexpected client data
    origin" because the browser-signed clientDataJSON.origin (the
    public URL) doesn't match the server-computed expected_origin
    (the internal one). Honouring X-Forwarded-Host on this side keeps
    rp_id + origin in lock-step.

    Also trusts ``X-Forwarded-Proto`` so HTTPS termination at NPM is
    visible to the verifier.
    """
    proto = (request.headers.get("x-forwarded-proto", "")
             or request.url.scheme or "http").split(",")[0].strip().lower()
    if proto not in ("http", "https"):
        # reject bogus X-Forwarded-Proto values
        # (e.g. "ftp", "file") instead of silently flipping to https.
        # Falls back to the actual request scheme; logs once so a
        # mis-configured proxy is debuggable from Admin → Logs.
        bad = proto
        proto = (request.url.scheme or "http").lower()
        if proto not in ("http", "https"):
            proto = "http"
        print(
            f"[webauthn] rejecting X-Forwarded-Proto={bad!r} "
            f"(not http/https) — falling back to scheme={proto!r}"
        )
    host_candidates = [
        request.headers.get("x-forwarded-host", ""),
        request.headers.get("host", ""),
        request.url.netloc or "",
        request.url.hostname or "",
    ]
    host_header = ""
    for raw in host_candidates:
        cand = (raw or "").split(",")[0].strip()
        if cand:
            host_header = cand
            break
    return f"{proto}://{host_header}"


@app.post("/api/local-auth/login")
async def api_local_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """Local-auth login: validate password + 2FA gate; mint session cookie on success."""
    ip = auth.client_ip(request)
    # check both the IP-only bucket AND the
    # (ip, username) bucket. The latter scopes lockout to the actual
    # user being typo'd at, so a corporate-NAT'd office isn't
    # collateral-damaged by one user's bad password.
    auth.rate_limit_check(ip, username)
    with db_conn() as c:
        u = auth.get_user_by_username(c, username)
        # split the failure cases for clearer operator-facing
        # error messages without disclosing username existence.
        # SECURITY: only specialise the message AFTER a successful
        # password verification; otherwise an attacker could enumerate
        # disabled accounts by probing for the "Account disabled"
        # response without knowing the password.
        password_ok = (
            u is not None
            and u.auth_source == "local"
            and auth.verify_password(password, _get_user_password_hash(c, u.id))
        )
        if not password_ok:
            auth.rate_limit_record_failure(ip, username)
            raise HTTPException(status_code=401, detail="Invalid credentials")
        # `password_ok` true implies `u is not None` (the `and u is not
        # None` short-circuit above), but the type-checker doesn't carry
        # that narrowing across the boolean expression. Explicit assert
        # so every `u.<field>` access below is well-typed.
        assert u is not None
        if u.disabled:
            # Password is verified correct; the user just can't log in.
            # Safe to disclose because we already proved the caller
            # holds the credentials. Use 403 so the SPA's login page
            # can branch on the status code if it ever wants per-case
            # styling. NOTE: we do NOT record a rate-limit failure
            # here — the credentials were CORRECT; the lockout exists
            # to slow down brute-force, not to punish a re-enable
            # attempt by a legitimate user.
            raise HTTPException(
                status_code=403,
                detail="Account is disabled. Contact your administrator.",
            )
        # ----------------------------------------------------------------
        # 2FA gate. Branches before any
        # session cookie is issued:
        # (a) user has TOTP enabled OR passkeys enrolled -> respond
        #     200 with step="totp_required" and methods=[...] so the
        #     SPA renders one of (or both) "Authenticator code" /
        #     "Use a passkey" inputs at the second-factor screen.
        # (b) policy requires 2FA for this role AND user has neither
        #     TOTP nor passkeys -> respond step="totp_setup_required"
        #     (forced TOTP enrolment; passkey-only enrolment-on-login
        #     isn't offered because it requires a roundtrip the
        #     legacy login form can't host).
        # (c) no 2FA, no requirement -> issue cookie (legacy path).
        # ----------------------------------------------------------------
        policy = _resolve_totp_policy()
        state = auth.get_user_totp_state(c, u.id)
        passkey_count = auth.count_user_credentials(c, u.id)
        # Master-toggle gates. When admin disables a method,
        # treat enrolled credentials of that type as if they don't
        # exist for login purposes — the method drops from `methods`
        # and is skipped in the has_2fa check. The user's enrolment
        # rows stay in the DB so flipping the toggle back on restores
        # the login path. If admin disables BOTH and the user has
        # nothing else, they fall through to single-factor (this is
        # the admin's explicit choice).
        totp_login_enabled = bool(state["enabled"]) and policy["totp_allowed"]
        passkey_login_enabled = (
            passkey_count > 0
            and policy["passkeys_allowed"]
            and webauthn_h.WEBAUTHN_AVAILABLE
        )
        has_2fa = totp_login_enabled or passkey_login_enabled
        # Lockout check happens BEFORE we mint a challenge so a locked
        # user gets a clear 423 rather than a stale challenge_id. Lockout
        # state is TOTP-only for now -- passkeys have their own per-IP
        # rate-limit on webauthn-finish failures. Skip when totp_allowed
        # is off (no point locking out a method we won't honour anyway).
        if totp_login_enabled and state["locked_until"]:
            if state["locked_until"] > int(time.time()):
                retry = state["locked_until"] - int(time.time())
                raise HTTPException(
                    status_code=423,
                    detail=(
                        "Account locked due to too many failed 2FA attempts. "
                        f"Try again in {max(1, retry // 60)} minute(s)."
                    ),
                    headers={"Retry-After": str(retry)},
                )
            # Lockout expired -- clear the state so the next failure
            # starts a fresh counter.
            auth.clear_totp_lockout(c, u.id)
        if has_2fa:
            methods: list[str] = []
            if totp_login_enabled:
                methods.append("totp")
            if passkey_login_enabled:
                methods.append("webauthn")
            cid, exp = _create_totp_challenge({
                "user_id": u.id,
                "kind": "totp_required",
                "ip": ip,
            })
            auth.rate_limit_clear(ip, username)
            return JSONResponse({
                "step": "totp_required",
                "challenge_id": cid,
                "expires_at": exp,
                "username": u.username,
                "methods": methods,
            })
        if policy["totp_allowed"] and _totp_required_for(u.role, policy):
            secret_plain = totp.generate_secret()
            uri = totp.provisioning_uri(secret_plain, u.username)
            cid, exp = _create_totp_challenge({
                "user_id": u.id,
                "kind": "totp_setup_required",
                "secret": secret_plain,
                "ip": ip,
            })
            auth.rate_limit_clear(ip, username)
            return JSONResponse({
                "step": "totp_setup_required",
                "challenge_id": cid,
                "expires_at": exp,
                "username": u.username,
                "secret": secret_plain,
                "provisioning_uri": uri,
            })
        # Legacy single-factor path.
        auth.rate_limit_clear(ip, username)
        auth.touch_last_login(c, u.id)
        cookie_value, expires_at = auth.create_session(
            c, u.id, ip, request.headers.get("user-agent"),
        )
        # Audit-trail row — first-class forensic record of the sign-in
        # (the Apprise notification above is a SEPARATE side-channel
        # opt-in; the history row is the canonical "who signed in
        # when" audit anchor).
        _ops_mod.write_admin_audit(
            c, "user_login",
            target_kind="user", target_name=u.username, target_id=u.username,
            actor=u.username,
            message=f"Signed in via local-auth from {ip}",
        )
    csrf = auth.generate_csrf_token()
    resp = JSONResponse({"username": u.username, "role": u.role, "source": u.auth_source})
    auth.set_session_cookie(resp, cookie_value, expires_at, request)
    auth.set_csrf_cookie(resp, csrf, expires_at, request)
    # Security event — opt-in via Admin → Notifications. Fire-and-
    # forget via the shared retry helper so a
    # transient Apprise blip doesn't drop the audit notification on
    # the floor.
    spawn_background_task(
        notify_with_retry(
            f"🔓 {u.username} signed in",
            f"via local from {ip}",
            event="user_login",
            actor_username=u.username,
            target_kind="user", target_id=u.username,
            metadata={"ip": ip, "method": "local"},
            label=f"user_login (local) {u.username!r}",
        ),
        label=f"user_login_notify {u.username!r}",
    )
    return resp


@app.post("/api/local-auth/totp")
async def api_local_login_totp(
    request: Request,
    challenge_id: str = Form(...),
    code: str = Form(...),
):
    """Step 2 of the multi-step login for users with TOTP enrolled.

    Verifies the 6-digit TOTP (or a backup code) against the user's
    stored secret, increments the per-user failure counter on miss,
    locks on threshold, and issues the og_session cookie on success.
    """
    ip = auth.client_ip(request)
    auth.rate_limit_check(ip)
    challenge = _peek_totp_challenge(challenge_id)
    if not challenge or challenge.get("kind") != "totp_required":
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400, detail="Invalid or expired challenge.",
        )
    user_id = challenge["user_id"]
    policy = _resolve_totp_policy()
    # Master toggle. When admin disables TOTP, refuse to verify
    # codes from already-enrolled users — defence in depth alongside
    # the api_local_login `methods` filter that already drops 'totp'
    # from the login response. A stale client could still POST here.
    if not policy["totp_allowed"]:
        _consume_totp_challenge(challenge_id)
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=403,
            detail=_err.message_for(_err.AUTH_TOTP_DISABLED_BY_ADMIN),
        )
    with db_conn() as c:
        u = auth.get_user(c, user_id)
        if not u or u.disabled or u.auth_source != "local":
            _consume_totp_challenge(challenge_id)
            auth.rate_limit_record_failure(ip)
            raise HTTPException(status_code=400, detail="User not eligible.")
        state = auth.get_user_totp_state(c, user_id)
        if state["locked_until"] and state["locked_until"] > int(time.time()):
            retry = state["locked_until"] - int(time.time())
            raise HTTPException(
                status_code=423,
                detail=(
                    "Account locked due to too many failed 2FA attempts. "
                    f"Try again in {max(1, retry // 60)} minute(s)."
                ),
                headers={"Retry-After": str(retry)},
            )
        secret_ct = auth.get_user_totp_secret(c, user_id)
        if not secret_ct:
            _consume_totp_challenge(challenge_id)
            raise HTTPException(status_code=400, detail="TOTP not enrolled.")
        try:
            secret_plain = totp.decrypt_secret(secret_ct)
        except Exception as e:
            print(f"[totp] decrypt secret FAILED for user {u.username}: {e}")
            raise HTTPException(status_code=500, detail="TOTP decrypt failed.")
        verified = False
        used_backup = False
        if totp.verify_code(secret_plain, code):
            verified = True
        else:
            matched, new_blob = totp.consume_backup_code(
                state["backup_codes_json"], code,
            )
            if matched and new_blob is not None:
                verified = True
                used_backup = True
                auth.update_user_totp_backup_codes(c, user_id, new_blob)
        if not verified:
            n, locked = auth.record_totp_failure(
                c, user_id,
                policy["totp_lockout_max_failures"],
                policy["totp_lockout_minutes"] * 60,
            )
            auth.rate_limit_record_failure(ip)
            print(f"[totp] {u.username} verify FAILED ({n}/{policy['totp_lockout_max_failures']})")
            if locked:
                print(f"[totp] {u.username} locked out for {policy['totp_lockout_minutes']}m")
                raise HTTPException(
                    status_code=423,
                    detail=(
                        "Account locked due to too many failed 2FA attempts. "
                        f"Try again in {policy['totp_lockout_minutes']} minute(s)."
                    ),
                )
            raise HTTPException(status_code=401, detail="Invalid code.")
        # Success path -- consume the challenge, clear lockout, issue cookie.
        _consume_totp_challenge(challenge_id)
        auth.clear_totp_lockout(c, user_id)
        auth.rate_limit_clear(ip)
        auth.touch_last_login(c, user_id)
        cookie_value, expires_at = auth.create_session(
            c, user_id, ip, request.headers.get("user-agent"),
            auth_method="totp",
        )
        # Audit-trail row — same shape as the legacy single-factor
        # path. The Apprise notification below is a side-channel.
        _ops_mod.write_admin_audit(
            c, "user_login",
            target_kind="user", target_name=u.username, target_id=u.username,
            actor=u.username,
            message=f"Signed in via local-auth (2FA TOTP{' + backup code' if used_backup else ''}) from {ip}",
        )
    if used_backup:
        print(f"[totp] {u.username} used backup code")
    else:
        print(f"[totp] {u.username} verified successfully")
    csrf = auth.generate_csrf_token()
    resp = JSONResponse({
        "username": u.username, "role": u.role, "source": u.auth_source,
    })
    auth.set_session_cookie(resp, cookie_value, expires_at, request)
    auth.set_csrf_cookie(resp, csrf, expires_at, request)
    try:
        await notify(
            f"🔓 {u.username} signed in",
            f"via local (2FA) from {ip}",
            event="user_login",
            actor_username=u.username,
            target_kind="user", target_id=u.username,
            metadata={"ip": ip, "method": "local_totp"},
        )
    except Exception as _e:
        print(f"[notify] user_login (totp) dropped: {_e}")
    return resp


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.post("/api/local-auth/totp-setup-confirm")
async def api_local_login_totp_setup_confirm(
    request: Request,
    challenge_id: str = Form(...),
    code: str = Form(...),
):
    """Step 2 of the multi-step login when policy is forcing enrolment.

    Verifies the freshly-typed 6-digit code against the secret we
    issued in step 1, persists the secret + backup codes, then issues
    the cookie. Returns the 10 plaintext backup codes (one-time reveal).
    """
    ip = auth.client_ip(request)
    auth.rate_limit_check(ip)
    challenge = _peek_totp_challenge(challenge_id)
    if not challenge or challenge.get("kind") != "totp_setup_required":
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400, detail="Invalid or expired challenge.",
        )
    user_id = challenge["user_id"]
    secret_plain = challenge.get("secret") or ""
    if not totp.verify_code(secret_plain, code):
        auth.rate_limit_record_failure(ip)
        raise HTTPException(status_code=401, detail="Invalid code.")
    backup_plain = totp.generate_backup_codes()
    encrypted_secret = totp.encrypt_secret(secret_plain)
    encrypted_codes_json = totp.encrypt_backup_codes(backup_plain)
    with db_conn() as c:
        u = auth.get_user(c, user_id)
        if not u or u.disabled or u.auth_source != "local":
            _consume_totp_challenge(challenge_id)
            raise HTTPException(status_code=400, detail="User not eligible.")
        auth.set_user_totp_secret(
            c, user_id, encrypted_secret, encrypted_codes_json,
        )
        _consume_totp_challenge(challenge_id)
        auth.rate_limit_clear(ip)
        auth.touch_last_login(c, user_id)
        cookie_value, expires_at = auth.create_session(
            c, user_id, ip, request.headers.get("user-agent"),
            auth_method="totp",
        )
        _ops_mod.write_admin_audit(
            c, "user_login",
            target_kind="user", target_id=u.username,
            actor=u.username,
            events_dict={
                "method": "local_totp_setup",
                "auth_source": u.auth_source,
                "ip": ip,
            },
        )
    print(f"[totp] {u.username} enrolled (forced by policy)")
    csrf = auth.generate_csrf_token()
    resp = JSONResponse({
        "username": u.username, "role": u.role, "source": u.auth_source,
        "backup_codes": backup_plain,
    })
    auth.set_session_cookie(resp, cookie_value, expires_at, request)
    auth.set_csrf_cookie(resp, csrf, expires_at, request)
    try:
        await notify(
            f"🔓 {u.username} signed in",
            f"via local (2FA enrolled) from {ip}",
            event="user_login",
            actor_username=u.username,
            target_kind="user", target_id=u.username,
            metadata={"ip": ip, "method": "local_totp_setup"},
        )
    except Exception as _e:
        print(f"[notify] user_login (totp setup) dropped: {_e}")
    return resp


# ============================================================================
# Login passkey routes. Pair with the existing TOTP routes above —
# both consume the same challenge-id minted in api_local_login. The login
# flow's "second factor" pivots on which method the SPA POSTs back:
# /api/local-auth/totp for a 6-digit code, /api/local-auth/webauthn-* for
# a passkey assertion. CSRF is exempt because the caller doesn't have a
# session cookie yet (auth-optional path).
# ============================================================================
class WebauthnLoginStartIn(BaseModel):
    challenge_id: str


class WebauthnLoginFinishIn(BaseModel):
    challenge_id: str
    credential: dict  # raw PublicKeyCredential JSON from the SPA


@app.post("/api/local-auth/webauthn-start")
async def api_local_login_webauthn_start(
    body: WebauthnLoginStartIn,
    request: Request,
):
    """Step 2A of the multi-step login: hand the SPA a WebAuthn
    challenge to feed into ``navigator.credentials.get()``.

    Reads the user_id from the in-memory TOTP challenge (minted by
    api_local_login). Allows the user to switch between TOTP and
    passkey on the same screen without re-entering the password --
    the challenge_id is shared.

    Returns ``{options: <PublicKeyCredentialRequestOptions>, login_id}``.
    The SPA POSTs the assertion back via webauthn-finish with the
    same login_id.
    """
    if not webauthn_h.WEBAUTHN_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=_err.message_for(_err.AUTH_WEBAUTHN_LIBRARY_MISSING),
        )
    # Master toggle. Defence-in-depth — the SPA won't offer
    # the passkey method when this is off (login response omits
    # 'webauthn' from `methods`), but a stale client could still try.
    if not _resolve_totp_policy()["passkeys_allowed"]:
        raise HTTPException(
            status_code=403,
            detail=_err.message_for(_err.AUTH_PASSKEYS_DISABLED_BY_ADMIN),
        )
    ip = auth.client_ip(request)
    auth.rate_limit_check(ip)
    challenge = _peek_totp_challenge(body.challenge_id)
    if not challenge or challenge.get("kind") != "totp_required":
        raise HTTPException(
            status_code=400, detail="Invalid or expired challenge.",
        )
    user_id = challenge["user_id"]
    with db_conn() as c:
        u = auth.get_user(c, user_id)
        if not u or u.disabled or u.auth_source != "local":
            raise HTTPException(status_code=400, detail="User not eligible.")
        creds = auth.list_user_credentials(c, user_id)
    if not creds:
        raise HTTPException(
            status_code=400,
            detail="No passkeys enrolled for this account.",
        )
    rp_id = _request_rp_id(request)
    # detect credentials registered under a different domain.
    # WebAuthn binds credentials to their RP ID; if the operator
    # migrated OmniGrid between domains, stored credentials are still
    # in the DB but the browser correctly refuses to offer them on the
    # new domain — falling through to the QR / hybrid flow with no
    # explanation. Compute the orphaned set so the SPA can surface a
    # clear "re-enrol from Profile" hint above the Passkey button.
    # Empty `rp_id` on a credential row means "registered before this
    # column landed" — treat as unknown rather than
    # mismatched so the legacy creds don't fire spurious banners.
    orphaned = []
    matching = []
    # Loop variable `cred`, NOT `c`. The convention in `main.py`
    # is `c` = sqlite connection (the outer `with db_conn() as c:`
    # has exited, but reusing the name in the loop body would shadow
    # that and add reader hazard). Renamed throughout the loop + the
    # allowCredentials list comprehension below.
    for cred in creds:
        cred_rp = (cred.get("rp_id") or "").strip().lower()
        if cred_rp and cred_rp != rp_id.lower():
            orphaned.append({
                "id": cred["id"],
                "friendly_name": cred.get("friendly_name") or "",
                "rp_id": cred_rp,
            })
        else:
            matching.append(cred)
    rp_id_mismatch = len(orphaned) > 0 and len(matching) == 0
    # Build the assertion options against ALL stored credentials. Even
    # when every credential is orphaned, we still send the options so
    # the browser tries — the spec-correct outcome is still QR-fallback,
    # but the SPA surfaces the banner explaining WHY based on the
    # `rp_id_mismatch` flag below. If at least one matching credential
    # exists, restrict allowCredentials to those so the picker doesn't
    # waste a click on a stale credential.
    _allow_set = matching if matching else creds
    options, raw_challenge = webauthn_h.make_authentication_options(
        rp_id=rp_id,
        allowed_credentials=[
            {
                "credential_id": cred["credential_id"],
                "transports": cred["transports"],
            }
            for cred in _allow_set
        ],
    )
    login_id, expires_at = _create_webauthn_login_challenge({
        "user_id": user_id,
        "challenge_bytes": raw_challenge,
        "rp_id": rp_id,
        "origin": _request_origin(request),
        "ip": ip,
    })
    # surface the per-credential transports being sent so the
    # operator can grep server logs to verify the assertion-options
    # payload includes 'internal' (without it, macOS Safari/Chrome
    # default to the QR/hybrid flow regardless of `hints`).
    _allow = (options.get("allowCredentials") or []) if isinstance(options, dict) else []
    _transports_summary = [
        {"id_prefix": (c.get("id") or "")[:8], "transports": c.get("transports")}
        for c in _allow
    ]
    print(
        f"[webauthn] {u.username} login-start (rp_id={rp_id}) "
        f"hints={options.get('hints') if isinstance(options, dict) else None} "
        f"allow={_transports_summary}"
    )
    if orphaned:
        print(
            f"[webauthn] {u.username} login-start RP-ID mismatch "
            f"current={rp_id!r} orphaned={[(o['friendly_name'], o['rp_id']) for o in orphaned]} "
            f"matching={len(matching)} "
        )
    return JSONResponse({
        "options": options,
        "login_id": login_id,
        "expires_at": expires_at,
        "username": u.username,
        # surface the RP-ID mismatch state so the SPA's login
        # form can render a clear hint instead of letting the browser
        # silently fall through to QR. Only fires when EVERY stored
        # credential's rp_id differs from the current rp_id (any
        # matching cred → operator can still authenticate normally,
        # no banner needed). `orphaned_credentials` lists the
        # friendly names + their original rp_ids for context.
        "rp_id_mismatch": rp_id_mismatch,
        "orphaned_credentials": orphaned,
        "current_rp_id": rp_id,
    })


@app.post("/api/local-auth/webauthn-finish")
async def api_local_login_webauthn_finish(
    body: WebauthnLoginFinishIn,
    request: Request,
):
    """Step 2B: verify the passkey assertion + mint the session cookie.

    Same success path as ``/api/local-auth/totp``: ``touch_last_login``,
    ``create_session``, ``set_session_cookie`` + ``set_csrf_cookie``,
    fire the user_login notification. Failures land in the per-IP
    rate-limit counter so a stolen credential_id can't be brute-forced.
    """
    if not webauthn_h.WEBAUTHN_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Passkey support is not available on this server.",
        )
    ip = auth.client_ip(request)
    auth.rate_limit_check(ip)
    challenge = _peek_webauthn_login_challenge(body.challenge_id)
    if not challenge:
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400, detail="Invalid or expired challenge.",
        )
    user_id = challenge["user_id"]
    expected_challenge: bytes = challenge["challenge_bytes"]
    expected_rp_id: str = challenge["rp_id"]
    expected_origin: str = challenge["origin"]
    cred_payload = body.credential or {}
    raw_id = cred_payload.get("rawId") or cred_payload.get("id") or ""
    if not raw_id or not isinstance(raw_id, str):
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400,
            detail="Malformed assertion payload.",
        )
    try:
        credential_id_bytes = webauthn_h.b64u_decode(raw_id)
    except Exception:
        auth.rate_limit_record_failure(ip)
        raise HTTPException(
            status_code=400, detail="Malformed credential id.",
        )
    with db_conn() as c:
        u = auth.get_user(c, user_id)
        if not u or u.disabled or u.auth_source != "local":
            _consume_webauthn_login_challenge(body.challenge_id)
            auth.rate_limit_record_failure(ip)
            raise HTTPException(status_code=400, detail="User not eligible.")
        stored = auth.get_credential_by_credential_id(c, credential_id_bytes)
        if not stored or stored["user_id"] != user_id:
            _consume_webauthn_login_challenge(body.challenge_id)
            auth.rate_limit_record_failure(ip)
            raise HTTPException(
                status_code=401, detail="Unknown credential.",
            )
        try:
            verified = webauthn_h.verify_authentication(
                credential_json=cred_payload,
                expected_challenge=expected_challenge,
                expected_origin=expected_origin,
                expected_rp_id=expected_rp_id,
                public_key=stored["public_key"],
                current_sign_count=stored["sign_count"],
            )
        except Exception as e:
            _consume_webauthn_login_challenge(body.challenge_id)
            auth.rate_limit_record_failure(ip)
            print(f"[webauthn] {u.username} verify FAILED: {e}")
            raise HTTPException(
                status_code=401, detail="Passkey verification failed.",
            )
        # Success path -- consume both challenges, bump sign-count, issue cookie.
        _consume_webauthn_login_challenge(body.challenge_id)
        # Also drop the paired TOTP challenge so the user can't replay
        # it via the TOTP path. We don't know the challenge_id used for
        # webauthn-start (login_id is its own), but the TOTP one was
        # never consumed in webauthn-start -- prune by user_id.
        _prune_totp_challenges()
        for k, v in list(_totp_challenges.items()):
            if v.get("user_id") == user_id and v.get("kind") == "totp_required":
                _totp_challenges.pop(k, None)
        auth.update_credential_after_use(
            c, stored["id"], verified["new_sign_count"],
        )
        auth.rate_limit_clear(ip)
        auth.touch_last_login(c, user_id)
        cookie_value, expires_at = auth.create_session(
            c, user_id, ip, request.headers.get("user-agent"),
            auth_method="passkey",
        )
        # Audit-trail row — same shape as the legacy single-factor +
        # TOTP paths. The Apprise notification is a side-channel.
        _ops_mod.write_admin_audit(
            c, "user_login",
            target_kind="user", target_name=u.username, target_id=u.username,
            actor=u.username,
            message=f"Signed in via local-auth (2FA passkey/WebAuthn cred {stored['id']}) from {ip}",
        )
    print(f"[webauthn] {u.username} verified successfully (cred {stored['id']})")
    csrf = auth.generate_csrf_token()
    resp = JSONResponse({
        "username": u.username, "role": u.role, "source": u.auth_source,
    })
    auth.set_session_cookie(resp, cookie_value, expires_at, request)
    auth.set_csrf_cookie(resp, csrf, expires_at, request)
    try:
        await notify(
            f"🔓 {u.username} signed in",
            f"via local (passkey) from {ip}",
            event="user_login",
            actor_username=u.username,
            target_kind="user", target_id=u.username,
            metadata={"ip": ip, "method": "local_passkey"},
        )
    except Exception as _e:
        print(f"[notify] user_login (webauthn) dropped: {_e}")
    return resp


def _get_user_password_hash(conn, user_id: int):
    """Fetch password_hash directly — not exposed via the User dataclass."""
    r = conn.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
    return r["password_hash"] if r else None


@app.post("/api/local-auth/change-password")
async def api_change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    *,
    user: CurrentUser,
):
    """Let a logged-in local user rotate their own password.

    - Authentik users are directed to Authentik (no password stored here).
    - Invalidates every other session for this user; keeps the caller's.
    - Rate-limited via the shared login limiter so brute-forcing the current
      password from a compromised session is bounded.
    """
    if user.auth_source != "local":
        raise HTTPException(
            status_code=400,
            detail="Authentik users must change their password in Authentik.",
        )
    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="New passwords do not match.")
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be 8+ characters.")
    if new_password == current_password:
        raise HTTPException(status_code=400, detail="New password must differ from the current one.")

    ip = auth.client_ip(request)
    auth.rate_limit_check(ip)

    with db_conn() as c:
        stored = _get_user_password_hash(c, user.id)
        if not auth.verify_password(current_password, stored):
            auth.rate_limit_record_failure(ip)
            raise HTTPException(status_code=401, detail="Current password is incorrect.")
        auth.rate_limit_clear(ip)
        # Preserve the caller's own session while invalidating others.
        current_token_id = None
        cookie = request.cookies.get(auth.COOKIE_NAME)
        if cookie:
            current_token_id = auth.parse_session_cookie(cookie)
        auth.change_password(c, user.id, new_password, keep_session_token=current_token_id)

    return {"status": "ok"}


@app.post("/api/local-auth/logout")
async def api_local_logout(request: Request):
    """Revoke the caller's session cookie + clear the browser cookie."""
    cookie = request.cookies.get(auth.COOKIE_NAME)
    actor = _actor_from(request)
    if cookie:
        token_id = auth.parse_session_cookie(cookie)
        if token_id:
            with db_conn() as c:
                auth.delete_session(c, token_id)
                # Audit row — first-class forensic record of the
                # self-logout. `session_revoke` covers admin-initiated
                # session kills; this op_type covers user-initiated
                # ones so both flow into the same audit surface.
                _ops_mod.write_admin_audit(
                    c, "user_logout",
                    target_kind="user", target_name=actor, target_id=actor,
                    actor=actor,
                    message="Signed out via local-auth logout",
                )
    resp = JSONResponse({"ok": True})
    auth.clear_session_cookies(resp, request)
    return resp


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.post("/api/local-auth/bootstrap")
async def api_local_bootstrap(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """One-shot: only works while the users table is empty.

    Lets operators claim the first admin on a fresh install without having
    to set BOOTSTRAP_ADMIN_* env vars. Self-disables as soon as any user
    exists — every subsequent call returns 403.
    """
    ip = auth.client_ip(request)
    auth.rate_limit_check(ip)
    with db_conn() as c:
        if auth.count_users(c) > 0:
            auth.rate_limit_record_failure(ip)
            raise HTTPException(status_code=403, detail="Bootstrap already consumed")
        if not username or not password or len(password) < 8:
            raise HTTPException(status_code=400, detail="Username required; password must be 8+ chars")
        u = auth.create_user(c, username, None, password, "admin", "local")
        auth.touch_last_login(c, u.id)
        cookie_value, expires_at = auth.create_session(
            c, u.id, ip, request.headers.get("user-agent"),
            auth_method="bootstrap",
        )
        _ops_mod.write_admin_audit(
            c, "user_login",
            target_kind="user", target_id=u.username,
            actor=u.username,
            events_dict={
                "method": "bootstrap",
                "auth_source": "local",
                "ip": ip,
            },
        )
    csrf = auth.generate_csrf_token()
    resp = JSONResponse(
        {"ok": True, "username": u.username, "role": u.role},
        status_code=201,
    )
    auth.set_session_cookie(resp, cookie_value, expires_at, request)
    auth.set_csrf_cookie(resp, csrf, expires_at, request)
    return resp


# noinspection PyTypeChecker,PyDictCreation,PyUnresolvedReferences
@app.get("/api/me")
async def api_me(request: Request):
    """Return the current identity if any. Auth-optional — returns
    {authenticated: false} instead of 401 so the SPA can decide whether
    to redirect to /login. For real users, includes the full profile
    (display_name, bio, avatar_url, timestamps) so the profile page can
    render from a single fetch.
    """
    user = getattr(request.state, "user", None)
    if not user:
        return {"authenticated": False}
    user = cast(auth.User, user)  # rebind with non-Optional type — PyCharm honors cast across the function body
    # API-token "users" have negative ids (see _resolve_user) — skip the
    # profile read for them, there's nothing in the users table.
    profile = None
    if user.id >= 0:
        with db_conn() as c:
            profile = auth.get_user_profile(c, user.id)
    out = {
        "authenticated": True,
        "username": user.username,
        "role": user.role,
        "source": user.auth_source,
        # Client-side runtime knobs — read once on init, applied to the
        # next poll iteration. Resolved per-request so an Admin → Config
        # save takes effect on the next /api/me round-trip without a
        # page reload. Add new client-tunables here rather than via a
        # separate endpoint.
        "client_config": {
            # Tunable is stored as integer seconds for operator-
            # friendly UI; multiply by 1000 here so the SPA's setTimeout
            # consumer keeps its existing ms-based contract. Renaming
            # the SPA field would touch every call site for no gain.
            "ops_poll_ms": tuning.tuning_int(Tunable.OPS_POLL_INTERVAL_SECONDS) * 1000,
            # SPA's loadHosts() reads this and uses it as the cap on
            # parallel /api/hosts/one/<id> calls during fan-out. Resolved
            # per /api/me round-trip so an Admin → Config save takes
            # effect on the next call.
            "hosts_parallel_fetch": tuning.tuning_int(Tunable.HOSTS_PARALLEL_FETCH),
            # Idle-time progressive fill cadence (seconds). When the
            # operator is on the Hosts view and stays at the top
            # without scrolling, a background ticker trickles
            # not-yet-loaded host rows through the shared refresh
            # queue at this cadence so by the time they scroll, the
            # data is already there. 0 disables (scroll-only lazy
            # load). Goes through the same `hosts_parallel_fetch`
            # cap so backend pressure stays bounded.
            "hosts_idle_fill_seconds": tuning.tuning_int(Tunable.HOSTS_IDLE_FILL_INTERVAL_SECONDS),
            # AI Assistant sidebar drawer width (px). SPA's
            # ai-sidebar-drawer reads this and applies via inline
            # style on the <aside> root. Mobile layout ignores it.
            "ai_sidebar_width_px": tuning.tuning_int(Tunable.AI_SIDEBAR_WIDTH_PX),
            # AI sidebar conversation-persist cadence (ms). Consumed by
            # the SPA's `_aiPersistInterval` setup — see static/js/app.js.
            "ai_conversation_persist_ms": tuning.tuning_int(
                Tunable.AI_CONVERSATION_PERSIST_INTERVAL_MS
            ),
            # AI conversation export — gates the "Export TXT" /
            # "Export JSON" buttons in the AI sidebar header.
            # 0 = hide buttons, 1 = show. Default 1.
            "ai_conversation_export_enabled": bool(tuning.tuning_int(Tunable.AI_CONVERSATION_EXPORT_ENABLED)),
            # SSE freshness-watchdog idle threshold. Stored as
            # seconds; SPA's `_sseIdleThresholdMs` consumer wants ms.
            "sse_idle_threshold_ms": tuning.tuning_int(Tunable.SSE_IDLE_THRESHOLD_SECONDS) * 1000,
            # pollOps SSE-up keep-alive cadence. Same ms-conversion
            # pattern as ops_poll_ms and sse_idle_threshold_ms.
            "pollops_sse_keepalive_ms": tuning.tuning_int(Tunable.POLLOPS_SSE_KEEPALIVE_SECONDS) * 1000,
            # SPA-side load-busy watchdog cap (ms). `_runWithBusy` and
            # the topbar `refresh()` / `loadHosts()` flow + the SSE-pill
            # refreshing flags (`cacheRefreshing` / `hubProbing` /
            # `statsRefreshing`) cap any individual "busy" indicator at
            # this many ms. Stored as seconds, multiplied here so the
            # SPA setTimeout call keeps its ms contract.
            "load_busy_max_ms": tuning.tuning_int(Tunable.LOAD_BUSY_MAX_SECONDS) * 1000,
            # stat-bar warn / crit cutovers. SPA's barLevel /
            # barColor helpers read these per-call so an Admin → Config
            # save lands on the next render. Stored as integer percent
            # (30..90 / 50..99).
            "stat_bar_warn_pct": tuning.tuning_int(Tunable.STAT_BAR_WARN_PCT),
            "stat_bar_crit_pct": tuning.tuning_int(Tunable.STAT_BAR_CRIT_PCT),
            # HTTP-probe TLS cert expiry warning threshold (days). SPA's
            # drawer card paints the expiry pill amber when remaining-
            # days < this; red when ≤ 0. Per-call read so an Admin →
            # Host stats save lands on the next drawer render.
            "http_probe_cert_warning_days": tuning.tuning_int(Tunable.HTTP_PROBE_CERT_WARNING_DAYS),
            # Notifications panel page size — SPA reads this as the
            # initial value of `notificationsLimit`. Operator-tunable
            # via Admin → Notifications. Range 5..200 enforced at
            # both write-time (TUNABLES bounds) and read-time
            # (`tuning_int` clamps).
            "notifications_page_size": tuning.tuning_int(Tunable.NOTIFICATION_PAGE_SIZE),
            # Notifications popup polling fallback cadence (seconds).
            # Consumed by the SPA's $watch on showNotificationsPopup —
            # only used when SSE is disconnected AND the popup is open.
            "notifications_poll_seconds": tuning.tuning_int(
                Tunable.NOTIFICATIONS_POLL_INTERVAL_SECONDS
            ),
            # Sampler tick cadence (used by the SNMP "warming up" banner
            # so the "~N min" hint reflects the operator's configured
            # interval rather than a stale literal). Stored as seconds;
            # the SPA renders minutes for display.
            "stats_sample_interval_seconds": tuning.tuning_int(Tunable.STATS_SAMPLE_INTERVAL_SECONDS),
            # SNMP-specific sampler cadence. When > 0, the SNMP
            # sampler runs at this interval instead of inheriting the
            # global stats_sample_interval. SPA's `snmpWarmingUpText`
            # uses this when non-zero so the "~N min" hint matches the
            # SNMP-specific cadence on operators who run SNMP at a
            # different cadence than Beszel/NE.
            "snmp_sample_interval_seconds": tuning.tuning_int(Tunable.SNMP_SAMPLE_INTERVAL_SECONDS),
            # Global SNMP per-host walk concurrency. Surfaced so the
            # Admin → Hosts editor can render the per-host
            # walk_concurrency input's placeholder as the resolved
            # global value (instead of a hardcoded "1") — operator
            # immediately sees what value the row will use when blank
            # vs the override they're typing.
            "snmp_per_host_walk_concurrency": tuning.tuning_int(Tunable.SNMP_PER_HOST_WALK_CONCURRENCY),
            # Global SNMP wall-clock budget — surfaced so the per-host
            # `wall_clock_budget` input's placeholder can render the
            # resolved global default ("Inherited: 60") instead of a
            # hardcoded literal.
            "snmp_wall_clock_budget_seconds": tuning.tuning_int(Tunable.SNMP_WALL_CLOCK_BUDGET_SECONDS),
            # Global ping defaults. Used by the SPA's metricSource()
            # tooltip so "Ping probe (TCP :443)" / "Ping probe (ICMP)"
            # falls back cleanly when a host has no per-host
            # ping_port / ping_transport override. Mirrors the SNMP
            # global-default surface pattern above.
            "ping": {
                "default_port": tuning.tuning_int(Tunable.PING_DEFAULT_PORT),
                "use_icmp": get_setting_bool(Settings.PING_USE_ICMP),
            },
            # Per-host drift baseline metric roster — single source of
            # truth for the SPA's drift-chip rendering. Backend's
            # `logic/host_baseline.py:METRICS` is canonical; surfacing
            # the tuple here lets the SPA iterate the API contract
            # instead of hardcoding a parallel literal. Adding a new
            # metric to METRICS (e.g. `swap_pct`) now propagates to
            # the SPA on the next `/api/me` round-trip without a
            # paired SPA edit.
            "baseline_metrics": list(_host_baseline.METRICS),
            # AI integration master state — surfaced so the SPA's
            # Cmd-K palette can decide whether to render the "Ask AI"
            # synthetic row. SPA gates on
            # `me.client_config.ai.enabled === true` AND
            # `me.client_config.ai.active_provider` being non-empty.
            "ai": {
                "enabled": get_setting_bool(Settings.AI_ENABLED),
                "active_provider": (get_setting(Settings.AI_ACTIVE_PROVIDER) or "").strip().lower(),
                "max_tokens": tuning.tuning_int(Tunable.AI_MAX_TOKENS),
                # Canonical provider list — the SPA's `aiProviderNames`
                # reads from this so adding a fifth provider is a
                # one-line edit to `logic.ai.SUPPORTED_PROVIDERS` and
                # every consumer (provider grid, settings form, the
                # active-provider dropdown) picks it up automatically
                # without a parallel SPA literal to keep in sync.
                "provider_names": list(_ai_supported_providers()),
            },
            # Last-Test-success timestamps per provider (DB-backed,
            # cross-browser / cross-machine). Stamped at the END of every
            # successful test endpoint via `_stamp_test_success`. Surfaced
            # here so the SPA's `lastTestSuccessLabel(key)` helper can
            # render "Last connected: <relative time>" next to every
            # Test connection button. Missing keys = no successful test
            # ever recorded; the SPA's `x-show` on the label collapses
            # cleanly. epoch seconds.
            "last_test_success": {
                key: int(get_setting(last_test_success_key(key), "0") or "0") or None
                for key in (
                    "portainer", "oidc", "beszel", "pulse",
                    "webmin", "snmp", "ping", "asset_inventory",
                )
            },
            # Scheduler-tz state so the admin Schedules tab can badge
            # "TZ: <name> → falling back to UTC" when the operator typed
            # an invalid IANA name. ``configured`` = raw setting,
            # ``resolved`` = active TZ (None on blank or invalid),
            # ``fallback`` = True only when configured was non-empty
            # but ZoneInfo rejected it.
            "scheduler_tz": schedules.scheduler_tz_state(),
            # per-provider chip colours. Hex string per provider,
            # falls back to the SPA's built-in default when the operator
            # hasn't customised. Read once on /api/me and applied to the
            # provider chip via inline `:style` (--chip-bg/-br/-fg).
            "provider_colors": {
                "beszel": get_setting(Settings.PROVIDER_COLOR_BESZEL) or "",
                "pulse": get_setting(Settings.PROVIDER_COLOR_PULSE) or "",
                "node_exporter": get_setting(Settings.PROVIDER_COLOR_NODE_EXPORTER) or "",
                "webmin": get_setting(Settings.PROVIDER_COLOR_WEBMIN) or "",
                "ping": get_setting(Settings.PROVIDER_COLOR_PING) or "",
            },
            # Canonical SNMP vendor key set — single source of truth at
            # ``logic.snmp._VALID_VENDOR_KEYS``. Surfaced so the Admin →
            # Hosts editor renders one checkbox per vendor without the
            # SPA hardcoding the list (was duplicated at three sites).
            # Adding a vendor in `_VENDOR_SIGNATURES` automatically
            # surfaces a checkbox here on the next /api/me round-trip.
            "snmp_vendor_keys": _snmp_vendor_keys_sorted(),
        },
    }
    # Surface the SESSION_SECRET-auto-generated state to admins.
    # When SESSION_SECRET isn't set in the env, logic/auth.py generates an
    # ephemeral one at boot — every container restart invalidates every
    # session. Today the only signal is a one-line print at boot, buried
    # in logs. Exposing this boolean lets the SPA render a dismissible
    # warning banner so operators know their sessions die on every redeploy.
    # Boolean only (no message string) — i18n surface lives in en.json.
    # Always included so the SPA can also clear a stale "dismissed" flag
    # once SESSION_SECRET is finally set in the env.
    out["session_secret_auto"] = (auth.auto_secret_warning() is not None)
    # bootstrap admin env vars still set in `.env` AFTER the
    # users table has been seeded. The bootstrap path is then a harmless
    # no-op on every restart, but two operational risks remain: (a) wiping
    # the DB and restarting would silently re-seed an admin from the env
    # values (surprise), (b) the password is sitting plaintext in `.env`.
    # Surfacing this boolean lets the SPA show a dismissible banner so
    # the operator clears the env vars before the next deploy.
    if BOOTSTRAP_ADMIN_USER and BOOTSTRAP_ADMIN_PASSWORD:
        with db_conn() as _bc:
            _user_n = auth.count_users(_bc)
        out["bootstrap_env_still_set"] = (_user_n > 0)
    else:
        out["bootstrap_env_still_set"] = False
    if profile:
        out.update({
            "id": profile["id"],
            "email": profile.get("email") or "",
            "display_name": profile.get("display_name") or "",
            "bio": profile.get("bio") or "",
            "created_at": profile.get("created_at"),
            "last_login_at": profile.get("last_login_at"),
            "avatar_url": f"/api/avatars/{profile['avatar_path']}" if profile.get("avatar_path") else None,
            # Per-user UI prefs. JSON dict — currently carries
            # `headerWeatherEnabled` / `headerClockEnabled` so toggling
            # them on desktop survives the trip to iPhone (or any other
            # browser) for the same login. Empty `{}` for users who've
            # never set anything; SPA falls back to its own per-toggle
            # defaults in that case.
            "ui_prefs": profile.get("ui_prefs") or {},
        })
        # Per-user notification opt-in map. Two-layer scoping:
        # the admin gate is shared via ``notify_events_admin`` so the
        # SPA can grey out toggles for events admin has globally
        # disabled; ``notify_events`` is the user's own resolved map
        # (defaults to admin state until the user opts out).
        admin_map = {
            name: get_setting_bool(
                notify_event_key(name), _NOTIFY_EVENT_DEFAULTS.get(name, True),
            )
            for name in _NOTIFY_EVENT_NAMES
        }
        with db_conn() as _c:
            user_prefs = auth.get_user_notify_prefs(_c, profile["id"])
        # Per-medium roster — every medium with a global enable toggle
        # AND a NOTIFY_MEDIUMS sender registered. Surfaced so the SPA
        # can render one Profile→Notifications column per available
        # medium without a separate /api/notify-mediums round-trip.
        from logic.ops import NOTIFY_MEDIUMS as _OPS_MEDIUMS
        from logic.ops import is_medium_enabled as _ops_medium_enabled
        notify_mediums = [
            {"name": m, "enabled": bool(_ops_medium_enabled(m))}
            for m in _OPS_MEDIUMS.keys()
        ]
        # Resolved per-event map: now `{event: bool | {medium: bool}}`
        # to mirror the per-medium granularity introduced for Profile→
        # Notifications. Three resolution shapes per event:
        # - User has stored a per-medium dict → return the dict (the
        #   SPA renders one checkbox per medium, defaults missing
        #   keys to True client-side).
        # - User has stored a bare bool (legacy, OR they opted out
        #   across every medium via the SPA's Disable-all bulk
        #   button) → return the bool.
        # - User has no stored value → fall back to the admin gate
        #   (the legacy "default to admin state" contract). Returned
        #   as a bare bool so the SPA renders the admin state across
        #   every medium column uniformly.
        resolved: dict = {}
        for name in _NOTIFY_EVENT_NAMES:
            if name in user_prefs:
                resolved[name] = user_prefs[name]
            else:
                resolved[name] = admin_map[name]
        out["notify_events"] = resolved
        out["notify_events_admin"] = admin_map
        out["notify_mediums"] = notify_mediums
        # Telegram link state — `null` when no Telegram user_id maps
        # to this username, otherwise the int user_id. The Profile
        # partial reads this to render either the "Generate link
        # code" button OR the "Linked as ..." chip + Unlink button.
        try:
            from logic import telegram_listener as _tg_listener
            _tg_mappings = _tg_listener.load_mappings()
            _tg_link_id: Optional[int] = None
            _tg_linked_at_ms: int = 0
            for _tg_id, _entry in _tg_mappings.items():
                if not isinstance(_entry, dict):
                    continue
                if _entry.get("username") == user.username:
                    try:
                        _tg_link_id = int(_tg_id)
                    except (TypeError, ValueError):
                        continue
                    _tg_linked_at_ms = int(_entry.get("linked_at_ms") or 0)
                    break
            out["telegram_link"] = (
                {
                    "telegram_user_id": _tg_link_id,
                    "linked_at_ms": _tg_linked_at_ms,
                }
                if _tg_link_id is not None else None
            )
        except Exception as _e:
            print(f"[me] telegram_link lookup failed: {_e}")
            out["telegram_link"] = None
        # TOTP / 2FA summary. Surfaced on /api/me so the SPA can
        # render the Profile section + the "Required by policy" banner
        # without a follow-up round-trip on every page load. Detailed
        # backup-codes payload still ships separately via /api/me/totp.
        _totp_policy = _resolve_totp_policy()
        with db_conn() as _c2:
            _totp_state = auth.get_user_totp_state(_c2, profile["id"])
            _passkey_count = auth.count_user_credentials(_c2, profile["id"])
        out["totp"] = {
            "enabled": bool(_totp_state["enabled"]),
            "allowed": bool(_totp_policy["totp_allowed"]),
            "required": (
                user.auth_source == "local"
                and _totp_required_for(user.role, _totp_policy)
            ),
        }
        # Passkeys. The SPA uses ``count`` as a quick hint
        # (e.g. show "+ Add passkey" when 0; show the list inline when
        # >0) without the full /api/me/webauthn round-trip. ``supported``
        # is the server-side capability flag (False when the webauthn
        # library is missing).
        out["passkeys"] = {
            "count": int(_passkey_count),
            "supported": (
                user.auth_source == "local"
                and webauthn_h.WEBAUTHN_AVAILABLE
            ),
            # Admin master toggle. When false, the SPA hides /
            # disables the "Add a passkey" button. Existing enrolments
            # remain visible + login-eligible until each user revokes.
            "allowed": bool(_totp_policy["passkeys_allowed"]),
        }
    return out


class UiPrefsIn(BaseModel):
    """Partial-update payload for PATCH /api/me/ui-prefs.

    Free-form dict — keys are SPA-defined (e.g. headerWeatherEnabled).
    Send `null` for a key to delete it from the stored prefs (so the
    SPA falls back to its default).
    """
    prefs: dict


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.patch("/api/me/ui-prefs")
async def api_me_ui_prefs(body: UiPrefsIn, request: Request):
    """Merge `body.prefs` into the calling user's `ui_prefs`.

    Auth required (cookie or token). API-token "users" (negative ids)
    can't store prefs — return 400. Returns the merged prefs so the
    SPA can confirm what's persisted.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Authentication required")
    user = cast(auth.User, user)  # rebind with non-Optional type — PyCharm honors cast across the function body
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot store UI prefs")
    with db_conn() as c:
        merged = auth.update_ui_prefs(c, user.id, body.prefs)
    return {"ui_prefs": merged}


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.post("/api/me/telegram-link-code")
async def api_me_telegram_link_code(request: Request):
    """Mint a one-time, 15-minute, single-use code the user pastes into
    Telegram's `/link <code>` to bind their Telegram user_id to their
    OmniGrid account.

    Code is 6 digits (zero-padded) for easy typing on mobile. Stored in
    `users.ui_prefs.telegram_link_code` + `_expires_ms`. Calling this
    endpoint again before expiry replaces the previous code with a
    fresh one (operator-visible "Regenerate" semantics).

    Auth required — cookie or token. API tokens (negative ids) cannot
    link a Telegram account (no `ui_prefs` to read).
    """
    import secrets as _secrets
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Authentication required")
    user = cast(auth.User, user)  # rebind with non-Optional type — PyCharm honors cast across the function body
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot link Telegram accounts")
    # 6-digit numeric — easy to type on mobile, ~1M-entry space is
    # plenty when codes expire in 15 minutes and are single-use.
    code = f"{_secrets.randbelow(1_000_000):06d}"
    expires_ms = int(time.time() * 1000) + 15 * 60 * 1000  # +15 minutes
    with db_conn() as c:
        merged = auth.update_ui_prefs(c, user.id, {
            "telegram_link_code": code,
            "telegram_link_code_expires_ms": expires_ms,
        })
    return {
        "code": code,
        "expires_ms": expires_ms,
        "ui_prefs": merged,
    }


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.delete("/api/me/telegram-link")
async def api_me_telegram_unlink(request: Request):
    """Remove the calling user's Telegram mapping (operator-side
    counterpart to `/unlink` issued from Telegram). Walks the
    `telegram_user_mappings` JSON and drops every entry mapping any
    Telegram user_id to this OmniGrid username.

    Auth required.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Authentication required")
    user = cast(auth.User, user)  # rebind with non-Optional type — PyCharm honors cast across the function body
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage Telegram links")
    from logic import telegram_listener as _tg_listener
    mappings = _tg_listener.load_mappings()
    target_username = user.username
    removed: list[str] = []
    for tg_id, entry in list(mappings.items()):
        if isinstance(entry, dict) and entry.get("username") == target_username:
            mappings.pop(tg_id, None)
            removed.append(tg_id)
    if removed:
        _tg_listener.save_mappings(mappings)
    return {"removed": removed}


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.post("/api/me/ui-prefs/beacon")
async def api_me_ui_prefs_beacon(body: UiPrefsIn, request: Request):
    """Beacon-friendly variant of PATCH /api/me/ui-prefs.

    `navigator.sendBeacon` only supports POST, can't set custom
    headers (so CSRF tokens via header don't work), and the request
    is fire-and-forget on the page-unload path. This endpoint accepts
    the same body shape but is registered as POST and is added to
    the CSRF exemption set in the auth middleware so unload-time
    chat-conversation saves land cleanly.

    Same auth gate as the PATCH variant — cookie session must be
    valid; API tokens can't write prefs. Same merge semantics.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Authentication required")
    user = cast(auth.User, user)  # rebind with non-Optional type — PyCharm honors cast across the function body
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot store UI prefs")
    with db_conn() as c:
        merged = auth.update_ui_prefs(c, user.id, body.prefs)
    return {"ui_prefs": merged}


@app.patch("/api/me/notify-prefs")
async def api_me_notify_prefs(
    request: Request,
    user: CurrentUser,
):
    """Per-user opt-in/out for the per-event notification preferences.

    Layered ON TOP of the admin-side ``notify_event_*`` gates: a
    notification fires only when (admin enabled) AND (user opted-in,
    or hasn't expressed a pref → defaults to admin state). Refuses to
    set a pref to True (or any per-medium bool=True) for an event the
    admin has globally disabled — the model only narrows DOWN.

    Payload shapes (free-form JSON dict — Pydantic validation is
    bypassed because the per-medium dict shape is operator-extensible
    via ``NOTIFY_MEDIUMS`` and a rigid model would require a deploy
    every time a medium lands):

      - ``{"event": true|false}`` — legacy bare-bool; sets the event
        across every globally-enabled medium.
      - ``{"event": {"app": true, "apprise": false}}`` — per-medium
        routing. Missing medium keys default to True (medium added
        after the user's last save still fires by default; explicit
        opt-out is the only way to silence a medium).
      - Mixed shapes per call are fine — some events as bare bool,
        others as per-medium dicts.

    Unknown event names are rejected (400) so a SPA-side typo doesn't
    silently land a malformed pref.

    API-token "users" (negative ids) can't store prefs.
    """
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot store notify prefs")
    try:
        payload = await request.json()
    except (ValueError, TypeError):
        raise HTTPException(400, "request body must be JSON")
    if not isinstance(payload, dict):
        raise HTTPException(400, "request body must be a JSON object")
    # Admin gate snapshot — refuse opt-IN for admin-disabled events.
    admin_map = {
        name: get_setting_bool(
            notify_event_key(name), _NOTIFY_EVENT_DEFAULTS.get(name, True),
        )
        for name in _NOTIFY_EVENT_NAMES
    }
    # Validate every event + value-shape BEFORE writing so a partial
    # save can't leave the user's prefs in a half-applied state.
    valid_event_names = set(_NOTIFY_EVENT_NAMES)
    cleaned: dict = {}
    for ev_name, value in payload.items():
        if ev_name not in valid_event_names:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown event '{ev_name}'.",
            )
        if value is None:
            # Skip — same as "leave unchanged", per the legacy contract.
            continue
        if isinstance(value, bool):
            if value is True and admin_map.get(ev_name) is False:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Event '{ev_name}' is disabled by admin; "
                        f"cannot enable per-user."
                    ),
                )
            cleaned[ev_name] = value
        elif isinstance(value, dict):
            per_medium: dict = {}
            for med_name, med_val in value.items():
                if not isinstance(med_val, bool):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Per-medium value for '{ev_name}.{med_name}' "
                            f"must be a boolean."
                        ),
                    )
                if med_val is True and admin_map.get(ev_name) is False:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Event '{ev_name}' is disabled by admin; "
                            f"cannot enable per-user (any medium)."
                        ),
                    )
                per_medium[str(med_name)] = bool(med_val)
            if per_medium:
                cleaned[ev_name] = per_medium
            else:
                # Empty per-medium dict is treated as "no explicit
                # choice" and dropped from the merge below — equivalent
                # to clearing the event's pref. Log it explicitly so an
                # operator investigating "why did my notify pref not
                # save?" has a breadcrumb in the persistent log without
                # having to instrument the SPA. The actor's username is
                # included so the log line is grep-friendly per user.
                print(
                    f"[notify] empty per-medium dict for "
                    f"'{user.username}'.'{ev_name}' — treated as "
                    f"'no explicit choice' (event pref unchanged)"
                )
        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Value for '{ev_name}' must be a boolean OR an "
                    f"object mapping medium → boolean."
                ),
            )
    # Read-modify-write so unspecified events keep their stored value.
    with db_conn() as c:
        current = auth.get_user_notify_prefs(c, user.id)
        merged = dict(current)
        for ev_name, value in cleaned.items():
            merged[ev_name] = value
        auth.set_user_notify_prefs(c, user.id, merged)
    # Per-medium roster echoed back so the SPA can re-render the grid
    # without a separate /api/me round-trip.
    from logic.ops import NOTIFY_MEDIUMS as _OPS_MEDIUMS
    from logic.ops import is_medium_enabled as _ops_medium_enabled
    notify_mediums = [
        {"name": m, "enabled": bool(_ops_medium_enabled(m))}
        for m in _OPS_MEDIUMS.keys()
    ]
    # Resolved map mirrors api_get_me's shape exactly so the SPA can
    # drop the response straight into state.
    resolved: dict = {}
    for name in _NOTIFY_EVENT_NAMES:
        if name in merged:
            resolved[name] = merged[name]
        else:
            resolved[name] = admin_map[name]
    return {
        "notify_events": resolved,
        "notify_events_admin": admin_map,
        "notify_mediums": notify_mediums,
    }


class ProfileIn(BaseModel):
    display_name: Optional[str] = None
    bio: Optional[str] = None
    email: Optional[str] = None


@app.patch("/api/me/profile")
async def api_update_profile(
    p: ProfileIn,
    user: CurrentUser,
):
    """Update the caller's own display_name / bio / email. Authentik users
    CAN edit these locally — those values don't round-trip to Authentik,
    they're OmniGrid's own overlay for display purposes.
    """
    # Keep the fields bounded so someone can't store a MB of biography.
    if p.display_name is not None and len(p.display_name) > 80:
        raise HTTPException(status_code=400, detail="display_name must be 80 chars or less")
    if p.bio is not None and len(p.bio) > 500:
        raise HTTPException(status_code=400, detail="bio must be 500 chars or less")
    if p.email is not None and p.email and len(p.email) > 200:
        raise HTTPException(status_code=400, detail="email must be 200 chars or less")
    with db_conn() as c:
        auth.update_user_profile(
            c, user.id,
            display_name=p.display_name,
            bio=p.bio,
            email=p.email,
        )
    return {"ok": True}


# Avatars live on the data volume next to the SQLite DB — persists across
# container restarts and redeploys. Keep the path out of user control:
# filename is derived from user id + content-type extension only.
_AVATAR_DIR = os.path.join(os.path.dirname(DB_PATH), "avatars")
os.makedirs(_AVATAR_DIR, exist_ok=True)
_AVATAR_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
    "image/gif": "gif", "image/webp": "webp",
}
_AVATAR_MAX_BYTES = 1_000_000  # 1 MB — avatars are small, reject uploads above


@app.post("/api/me/avatar")
async def api_upload_avatar(
    request: Request,
    user: CurrentUser,
):
    """Accept a multipart image upload and store it under /app/data/avatars/.

    Validates content-type against an allowlist, caps at 1 MB, and writes
    a filename of the form `u<id>.<ext>` so the same user always overwrites
    their previous avatar (no stale files left around).
    """
    form = await request.form()
    file_field = form.get("file")
    # Starlette's `form.get` returns `UploadFile | str | None`. Narrow
    # to UploadFile via duck-typing on `.read` so the type-checker
    # accepts `.content_type` / `.read()` access below.
    if file_field is None or isinstance(file_field, str) or not hasattr(file_field, "read"):
        raise HTTPException(status_code=400, detail="Field 'file' missing")
    file = file_field  # type: ignore[assignment]  # narrowed to UploadFile via the isinstance + hasattr guard above
    ct = (file.content_type or "").lower()
    ext = _AVATAR_EXT.get(ct)
    if not ext:
        raise HTTPException(
            status_code=400,
            detail="Unsupported image type. Allowed: PNG / JPEG / GIF / WEBP.",
        )
    data = await file.read()
    if len(data) > _AVATAR_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Image too large (max 1 MB)")
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty upload")
    # Clean up any existing avatar at a different extension.
    with db_conn() as c:
        old = auth.get_user_profile(c, user.id)
    if old and old.get("avatar_path"):
        old_full = os.path.join(_AVATAR_DIR, old["avatar_path"])
        if os.path.exists(old_full) and old["avatar_path"] != f"u{user.id}.{ext}":
            try:
                os.remove(old_full)
            except OSError:
                pass
    fname = f"u{user.id}.{ext}"
    with open(os.path.join(_AVATAR_DIR, fname), "wb") as f:
        f.write(data)
    with db_conn() as c:
        auth.set_user_avatar_path(c, user.id, fname)
    return {"ok": True, "avatar_url": f"/api/avatars/{fname}"}


_AVATAR_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
# Strict canonical-form regex for avatar filenames the upload path
# emits (`u<int_id>.<ext>` where ext is one of the allowlist values
# in `_AVATAR_EXT.values()`). Used by `_avatar_path_from_fname` to
# parse the URL-segment into primitives (int + allowlisted string)
# so the joined path has NO operator-controlled string taint flowing
# into it — CodeQL's path-injection tracker is happier with primitive
# rebuilding than with a regex-+-realpath sanitizer chain.
_AVATAR_FNAME_CANONICAL = re.compile(r"^u(?P<uid>\d+)\.(?P<ext>png|jpg|jpeg|gif|webp)$")


def _avatar_path_from_fname(fname: str) -> Optional[str]:
    """Parse a strict canonical avatar URL segment (`u<id>.<ext>`)
    and rebuild the on-disk path from PRIMITIVES — int user_id plus
    an allowlisted extension string. Returns None when the input
    doesn't match the canonical shape.

    This is the CodeQL-friendly sanitizer for avatar serving: the
    returned path is built from `_AVATAR_DIR` (constant) + an int
    converted via `int()` (CodeQL drops the string-taint label on
    type conversion) + a string drawn from a closed allowlist (the
    second regex group can ONLY be one of the literal alternation
    branches). Any non-canonical input — including all operator-
    typed escapes, separators, ``..``, NUL bytes, etc. — fails the
    regex up-front and returns None.
    """
    if not isinstance(fname, str) or not fname:
        return None
    m = _AVATAR_FNAME_CANONICAL.fullmatch(fname)
    if not m:
        return None
    # int() conversion is the canonical taint-stripper for numeric
    # operator input — CodeQL recognises it as a barrier.
    try:
        uid = int(m.group("uid"))
    except (TypeError, ValueError):
        return None
    if uid <= 0:
        return None
    ext = m.group("ext")
    # Defence-in-depth: re-validate against the closed allowlist
    # even though the regex above already enforces it. CodeQL sees
    # `ext` as a regex group result; a `value in CONSTANT_SET` check
    # is one of its sanitiser patterns.
    if ext not in _AVATAR_EXT.values():
        return None
    return os.path.join(_AVATAR_DIR, f"u{uid}.{ext}")


def _safe_avatar_path(name: str) -> Optional[str]:
    """Resolve `<_AVATAR_DIR>/<name>` and confirm the result stays
    within `_AVATAR_DIR`. Returns the realpath on success or None
    when the input fails the strict shape regex OR the resolved path
    escapes the root (symlink / corrupt DB row).

    Four-layer defence-in-depth so CodeQL's taint tracker recognises
    every barrier (the prior `startswith(root + os.sep)` check was
    correct but not in CodeQL's sanitizer list, and CodeQL kept
    flagging downstream filesystem reads as ``py/path-injection``):

    1. **Type + emptiness guard** — bail on anything that isn't a
       non-empty string before touching any path API.
    2. **Strict allowlist regex** ``^[A-Za-z0-9._-]+$`` — no
       slashes, no backslashes, no leading dots that could form
       ``..``, no NUL bytes, no path separators of any flavour.
    3. **`os.path.basename`** — CodeQL recognises this as a
       canonical path-component sanitizer. The regex above already
       guarantees the input has no separators so the basename is a
       no-op on well-formed values, but the explicit call is what
       convinces the taint tracker the value is path-safe.
    4. **`os.path.commonpath` confinement** — re-canonicalises the
       joined path via `realpath` and confirms the joined result
       shares the avatar root as its common prefix. CodeQL
       recognises ``commonpath == root`` as an explicit barrier
       (versus the older ``startswith(root + os.sep)`` shape, which
       is correct semantically but isn't in the sanitizer list).
    """
    if not isinstance(name, str) or not name:
        return None
    # Layer 2 — strict regex.
    if not _AVATAR_SAFE_NAME.fullmatch(name):
        return None
    # Reject standalone `.` / `..` / leading dots (regex above
    # allows dots in the middle, e.g. `u5.png`, but `..` and `.`
    # would also match — extra explicit guard).
    if name in (".", "..") or name.startswith(".."):
        return None
    # Layer 3 — basename strip. No-op on regex-valid inputs but
    # registered as a sanitizer barrier in CodeQL's path-injection
    # query.
    safe_name = os.path.basename(name)
    if safe_name != name or not safe_name:
        return None
    root = os.path.realpath(_AVATAR_DIR)
    candidate = os.path.realpath(os.path.join(root, safe_name))
    # Layer 4 — commonpath confinement (recognised barrier).
    try:
        common = os.path.commonpath([root, candidate])
    except ValueError:
        # Different drives / mount points (Windows) / mixed separators
        # → can't share a common path → reject.
        return None
    if common != root:
        return None
    return candidate


@app.delete("/api/me/avatar")
async def api_clear_avatar(user: CurrentUser):
    """Clear the caller's avatar (deletes the file on disk)."""
    with db_conn() as c:
        p = auth.get_user_profile(c, user.id)
    if p and p.get("avatar_path"):
        # `avatar_path` originates as a user-uploaded basename; even
        # though the upload path stores only `u<id>.<ext>`, route
        # through the realpath-guarded resolver so a corrupt DB row
        # can't trick this delete into removing a file outside
        # `_AVATAR_DIR`.
        full = _safe_avatar_path(p["avatar_path"])
        if full and os.path.exists(full):
            try:
                os.remove(full)
            except OSError:
                pass
    with db_conn() as c:
        auth.set_user_avatar_path(c, user.id, None)
    return {"ok": True}


@app.get("/api/avatars/{fname}")
async def api_serve_avatar(fname: str, _user: CurrentUser):
    """Serve an uploaded avatar. Authed — avatars are user data, shouldn't
    be browsable anonymously.

    Path-traversal-guarded via `_avatar_path_from_fname` which parses
    the URL segment into PRIMITIVES (int user_id + allowlisted ext
    drawn from a closed regex-alternation set) and rebuilds the
    on-disk path from those — no operator-controlled string flows
    into the path-construction expression. Any non-canonical fname
    (separators, `..`, NUL bytes, escape sequences, anything outside
    the strict `u<int>.<ext>` shape) fails the regex up-front and
    returns 404.
    """
    full = _avatar_path_from_fname(fname)
    if not full or not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="Not found")
    # Derive content-type from the parsed extension. We re-parse here
    # rather than threading the ext through `_avatar_path_from_fname`
    # so the function stays single-return-typed (path-or-None);
    # parsing twice is cheap and keeps the API surface narrow.
    m = _AVATAR_FNAME_CANONICAL.fullmatch(fname)
    ext = m.group("ext") if m else "octet-stream"
    ct = next((k for k, v in _AVATAR_EXT.items() if v == ext), "application/octet-stream")
    return FileResponse(full, media_type=ct)


# ============================================================================
# Profile -> Two-factor authentication (TOTP) —.
# ============================================================================
class TotpEnrollConfirmIn(BaseModel):
    secret: str
    code: str


class TotpDisableIn(BaseModel):
    password: str


def _totp_authentik_guard(user: auth.User) -> None:
    if user.auth_source == "authentik":
        raise HTTPException(
            status_code=400,
            detail="Authentik users manage 2FA in their IdP.",
        )


def _totp_required_for_user(user: auth.User) -> bool:
    """Convenience wrapper around _totp_required_for() given a User.

    Honours the global role-based policy AND the per-user
    `totp_force_required` admin override. Either one is enough
    to require 2FA for this user. Authentik users always return False
    here — their auth_source short-circuits TOTP at the call sites.
    """
    if getattr(user, "auth_source", "local") != "local":
        return False
    if getattr(user, "totp_force_required", False):
        return True
    return _totp_required_for(user.role)


@app.get("/api/me/totp")
async def api_me_totp_status(user: CurrentUser):
    """Return the caller's 2FA status + decrypted backup codes.

    Backup codes are returned in plaintext (with a ``used_at`` flag per
    code) so the Profile page can render them under a hide/unhide
    eye toggle. Authentik users get a short-circuited reply that the
    SPA renders as "managed by IdP". API tokens (negative id) get 400.
    """
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage 2FA")
    policy = _resolve_totp_policy()
    if user.auth_source == "authentik":
        return {
            "auth_source": user.auth_source,
            "allowed": False,
            "enabled": False,
            "required": False,
            "backup_codes": [],
            "policy": policy,
        }
    with db_conn() as c:
        state = auth.get_user_totp_state(c, user.id)
    codes = totp.decrypt_backup_codes(state["backup_codes_json"])
    return {
        "auth_source": user.auth_source,
        "allowed": bool(policy["totp_allowed"]),
        "enabled": bool(state["enabled"]),
        "required": _totp_required_for_user(user),
        "backup_codes": codes,
        "policy": policy,
    }


@app.post("/api/me/totp/enroll-start")
async def api_me_totp_enroll_start(user: CurrentUser):
    """Generate a fresh secret + provisioning_uri for the caller.

    The secret is NOT persisted at this stage -- the SPA echoes it back
    via /api/me/totp/enroll-confirm so the user proves they captured
    it correctly before we lock it in.
    """
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage 2FA")
    _totp_authentik_guard(user)
    policy = _resolve_totp_policy()
    if not policy["totp_allowed"]:
        raise HTTPException(
            403, "Two-factor authentication is disabled by admin policy.",
        )
    secret_plain = totp.generate_secret()
    uri = totp.provisioning_uri(secret_plain, user.username)
    print(f"[totp] {user.username} enroll-start (secret prepared, awaiting confirm)")
    return {
        "secret": secret_plain,
        "provisioning_uri": uri,
        "username": user.username,
        "issuer": "OmniGrid",
    }


@app.post("/api/me/totp/enroll-confirm")
async def api_me_totp_enroll_confirm(
    body: TotpEnrollConfirmIn,
    user: CurrentUser,
):
    """Persist the secret + generate backup codes after a successful
    verification.

    Returns the 10 plaintext backup codes ONCE in this response. The
    Profile page also keeps them recoverable via /api/me/totp afterwards
    (encrypted at rest with the same Fernet key).
    """
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage 2FA")
    _totp_authentik_guard(user)
    policy = _resolve_totp_policy()
    if not policy["totp_allowed"]:
        raise HTTPException(
            403, "Two-factor authentication is disabled by admin policy.",
        )
    if not body.secret or len(body.secret) < 16:
        raise HTTPException(400, "Missing or malformed secret.")
    if not totp.verify_code(body.secret, body.code):
        raise HTTPException(401, "Invalid verification code.")
    backup_plain = totp.generate_backup_codes()
    encrypted_secret = totp.encrypt_secret(body.secret)
    encrypted_codes_json = totp.encrypt_backup_codes(backup_plain)
    with db_conn() as c:
        auth.set_user_totp_secret(
            c, user.id, encrypted_secret, encrypted_codes_json,
        )
        # Audit — user self-service TOTP enrolment is a security-sensitive
        # state change that admin-side ops can't see otherwise.
        try:
            _ops_mod.write_admin_audit(
                c, "totp_self_enroll",
                target_kind="auth", target_name=user.username, target_id=str(user.id),
                actor=user.username,
                message=f"TOTP enrolled by user {user.username}",
            )
        except Exception as e:
            print(f"[totp] self-enroll audit-row write failed: {e}")
    print(f"[totp] {user.username} enrolled")
    return {
        "ok": True,
        "backup_codes": backup_plain,
    }


@app.post("/api/me/totp/regenerate-codes")
async def api_me_totp_regenerate_codes(
    user: CurrentUser,
):
    """Replace the backup codes with a fresh batch of 10. Existing
    codes are discarded (used + unused alike). One-time reveal of the
    new plaintext list."""
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage 2FA")
    _totp_authentik_guard(user)
    with db_conn() as c:
        state = auth.get_user_totp_state(c, user.id)
        if not state["enabled"]:
            raise HTTPException(400, "Two-factor authentication is not enabled.")
        backup_plain = totp.generate_backup_codes()
        encrypted = totp.encrypt_backup_codes(backup_plain)
        auth.update_user_totp_backup_codes(c, user.id, encrypted)
        try:
            _ops_mod.write_admin_audit(
                c, "totp_self_regenerate_codes",
                target_kind="auth", target_name=user.username, target_id=str(user.id),
                actor=user.username,
                message=f"TOTP backup codes regenerated by user {user.username}",
            )
        except Exception as e:
            print(f"[totp] self-regenerate audit-row write failed: {e}")
    print(f"[totp] {user.username} regenerated backup codes")
    return {"ok": True, "backup_codes": backup_plain}


@app.post("/api/me/totp/disable")
async def api_me_totp_disable(
    body: TotpDisableIn,
    user: CurrentUser,
):
    """Self-disable 2FA after re-confirming the password.

    Refused when the admin policy currently requires TOTP for the
    user's role -- the operator must lift the policy first OR an
    admin must override. Authentik users 400.
    """
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage 2FA")
    _totp_authentik_guard(user)
    # 2FA is satisfied if EITHER TOTP OR a passkey is enrolled. So
    # a user with a passkey can self-disable TOTP even when policy
    # requires 2FA. Block ONLY when removing TOTP would leave the user
    # with no 2FA at all under a required-2FA policy.
    if _totp_required_for_user(user):
        with db_conn() as c:
            passkeys = auth.count_user_credentials(c, user.id)
        if passkeys == 0:
            raise HTTPException(
                403,
                "Admin policy requires 2FA for your role; "
                "enrol a passkey first or ask an admin to lift the policy.",
            )
    with db_conn() as c:
        stored = _get_user_password_hash(c, user.id)
        if not auth.verify_password(body.password, stored):
            raise HTTPException(401, "Current password is incorrect.")
        auth.clear_user_totp(c, user.id)
        try:
            _ops_mod.write_admin_audit(
                c, "totp_self_disable",
                target_kind="auth", target_name=user.username, target_id=str(user.id),
                actor=user.username,
                message=f"TOTP self-disabled by user {user.username}",
            )
        except Exception as e:
            print(f"[totp] self-disable audit-row write failed: {e}")
    print(f"[totp] {user.username} disabled")
    return {"ok": True}


# ============================================================================
# Profile -> WebAuthn / passkey management. Cookie-authed; CSRF
# enforced globally by the middleware. Authentik users 400 (their IdP
# manages MFA). API-token "users" (negative ids) 400.
# ============================================================================
class WebauthnRegisterStartIn(BaseModel):
    """Empty body -- the route reads username + user_id from the
    session. Kept as a model for future fields (e.g. preferred
    transports filter)."""
    pass


class WebauthnRegisterFinishIn(BaseModel):
    credential: dict
    friendly_name: Optional[str] = None


def _webauthn_self_guard(user: auth.User) -> None:
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage passkeys")
    if user.auth_source == "authentik":
        raise HTTPException(
            status_code=400,
            detail="Authentik users manage 2FA in their IdP.",
        )
    if not webauthn_h.WEBAUTHN_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=_err.message_for(_err.AUTH_WEBAUTHN_LIBRARY_MISSING),
        )


class WebauthnClientErrorIn(BaseModel):
    """Body for /api/me/webauthn/client-error — the SPA POSTs this when
    `navigator.credentials.create()` or `.get()` rejects with a
    DOMException so the failure reason lands in Admin → Logs.
    Fields are all best-effort strings; capped server-side to keep a
    misbehaving client from spamming the buffer.
    """
    phase: Optional[str] = None  # "register" | "login"
    error_name: Optional[str] = None  # DOMException.name
    error_message: Optional[str] = None
    rp_id: Optional[str] = None
    origin: Optional[str] = None


@app.post("/api/me/webauthn/client-error")
async def api_me_webauthn_client_error(
    body: WebauthnClientErrorIn,
    request: Request,
    user: CurrentUser,
):
    """Surface a client-side WebAuthn ceremony failure into the server
    log buffer. Pure logging — no DB write, no state change. Caps each
    field at 200 chars so a flooding client can't spam the ring."""

    def _trim(s: Optional[str]) -> str:
        s = (s or "").strip()
        return s[:200]

    phase = _trim(body.phase) or "?"
    err_name = _trim(body.error_name) or "?"
    err_msg = _trim(body.error_message)
    rp_id = _trim(body.rp_id) or _request_rp_id(request)
    origin = _trim(body.origin) or _request_origin(request)
    server_origin = _request_origin(request)
    server_rp_id = _request_rp_id(request)
    msg = (
        f"[webauthn] CLIENT ERROR — user={user.username} phase={phase} "
        f"error_name={err_name}"
    )
    if err_msg:
        msg += f" error_message={err_msg!r}"
    msg += (
        f" client_rp_id={rp_id} client_origin={origin} "
        f"server_rp_id={server_rp_id} server_origin={server_origin}"
    )
    print(msg)
    return {"ok": True}


@app.get("/api/me/webauthn")
async def api_me_webauthn_list(
    request: Request,
    user: CurrentUser,
):
    """Return every passkey enrolled for the caller.

    Each row is shaped ``{id, friendly_name, transports, created_at,
    last_used_at, sign_count, credential_id, rp_id}`` -- credential_id is
    base64url for display purposes only (stable identifier for the
    revoke button). public_key never leaves the server.

    ``rp_id`` lets the SPA flag credentials registered under a
    different domain (orphaned passkeys that the browser will refuse
    to offer at login). Profile → Security renders an inline badge
    when ``pk.rp_id !== current_rp_id``.
    """
    if user.id < 0:
        raise HTTPException(400, "API tokens cannot manage passkeys")
    if user.auth_source == "authentik":
        return {"auth_source": user.auth_source, "supported": False, "credentials": []}
    if not webauthn_h.WEBAUTHN_AVAILABLE:
        return {
            "auth_source": user.auth_source,
            "supported": False,
            "credentials": [],
            "error": "webauthn library not installed on the server.",
        }
    with db_conn() as c:
        rows = auth.list_user_credentials(c, user.id)
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "credential_id": webauthn_h.b64u_encode(r["credential_id"]),
            "friendly_name": r["friendly_name"],
            "transports": r["transports"],
            "created_at": r["created_at"],
            "last_used_at": r["last_used_at"],
            "sign_count": r["sign_count"],
            "rp_id": r.get("rp_id", "") or "",
        })
    return {
        "auth_source": user.auth_source,
        "supported": True,
        "credentials": out,
        # current effective rp_id so the SPA can compare each
        # credential's rp_id against the live page's domain WITHOUT
        # the SPA having to re-derive it (the SPA's `location.hostname`
        # would skip X-Forwarded-Host edge cases that `_request_rp_id`
        # handles).
        "current_rp_id": _request_rp_id(request),
    }


@app.post("/api/me/webauthn/register-start")
async def api_me_webauthn_register_start(
    request: Request,
    user: CurrentUser,
):
    """Hand the SPA ``PublicKeyCredentialCreationOptions``.

    The challenge is stashed in-memory keyed by user_id (5-min TTL).
    The SPA echoes back the authenticator response via register-finish
    -- if the user starts a second enrolment without finishing the
    first, the challenge is overwritten (last-wins; safe -- challenges
    are per-user and not consumable across users).
    """
    _webauthn_self_guard(user)
    # Admin master toggle. Only register-start is gated — list /
    # revoke / login still work for already-enrolled keys, mirroring
    # the totp_allowed shape (admin can flip enrolment off without
    # breaking active logins).
    if not _resolve_totp_policy()["passkeys_allowed"]:
        raise HTTPException(
            status_code=403,
            detail=_err.message_for(_err.AUTH_PASSKEYS_DISABLED_BY_ADMIN),
        )
    rp_id = _request_rp_id(request)
    rp_name = "OmniGrid"
    with db_conn() as c:
        creds = auth.list_user_credentials(c, user.id)
    existing_ids = [c["credential_id"] for c in creds]
    # WebAuthn user-handle: 1..64 bytes, opaque to the RP. Use the
    # numeric user id as a left-padded 4-byte blob -- stable per user,
    # never leaks PII.
    user_handle = f"omnigrid-user-{user.id}".encode()
    options, raw_challenge = webauthn_h.make_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_id=user_handle,
        username=user.username,
        display_name=user.username,
        existing_credential_ids=existing_ids,
    )
    expires_at = _set_webauthn_register_challenge(user.id, {
        "challenge_bytes": raw_challenge,
        "rp_id": rp_id,
        "origin": _request_origin(request),
    })
    print(
        f"[webauthn] {user.username} register-start "
        f"(rp_id={rp_id}, origin={_request_origin(request)})"
    )
    return {
        "options": options,
        "expires_at": expires_at,
        "rp_id": rp_id,
    }


@app.post("/api/me/webauthn/register-finish")
async def api_me_webauthn_register_finish(
    body: WebauthnRegisterFinishIn,
    _request: Request,
    user: CurrentUser,
):
    """Verify the attestation + persist the new credential row.

    Friendly name validation: 0-64 visible chars; empty -> default
    "Passkey N" where N = (existing count + 1) so the operator gets
    a sensible label even when the SPA forgot to prompt.
    """
    _webauthn_self_guard(user)
    state = _consume_webauthn_register_challenge(user.id)
    if not state:
        raise HTTPException(
            status_code=400, detail="Invalid or expired challenge.",
        )
    cred_payload = body.credential or {}
    if not isinstance(cred_payload, dict):
        raise HTTPException(
            status_code=400, detail="Malformed credential payload.",
        )
    try:
        result = webauthn_h.verify_registration(
            credential_json=cred_payload,
            expected_challenge=state["challenge_bytes"],
            expected_origin=state["origin"],
            expected_rp_id=state["rp_id"],
        )
    except Exception as e:
        print(f"[webauthn] {user.username} register verify FAILED: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Could not verify passkey: {e}",
        )
    try:
        friendly = webauthn_h.validate_friendly_name(body.friendly_name or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    with db_conn() as c:
        existing = auth.list_user_credentials(c, user.id)
        if not friendly:
            friendly = f"Passkey {len(existing) + 1}"
        # Duplicate check (UNIQUE on credential_id catches it too --
        # mapped to 409 here for the friendlier shape).
        for r in existing:
            if r["credential_id"] == result["credential_id"]:
                raise HTTPException(
                    status_code=409,
                    detail="This passkey is already enrolled.",
                )
        try:
            row_id = auth.add_user_credential(
                c,
                user_id=user.id,
                credential_id=result["credential_id"],
                public_key=result["public_key"],
                sign_count=result["sign_count"],
                transports=result["transports"],
                friendly_name=friendly,
                # stamp the rp_id this credential was registered
                # under so login can detect "credential registered under
                # a different domain" later. ``state["rp_id"]`` came
                # from `_request_rp_id(request)` at register-start
                # time, so it tracks the effective hostname the user
                # was on when they enrolled.
                rp_id=state.get("rp_id", "") or "",
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail="This passkey is already enrolled.",
            )
        try:
            _ops_mod.write_admin_audit(
                c, "passkey_self_register",
                target_kind="auth", target_name=user.username, target_id=str(row_id),
                actor=user.username,
                message=(f"passkey {friendly!r} registered by user {user.username} "
                         f"(rp_id={state.get('rp_id') or '?'})"),
            )
        except Exception as e:
            print(f"[webauthn] self-register audit-row write failed: {e}")
    print(f"[webauthn] {user.username} enrolled passkey "
          f"id={row_id} name={friendly!r}")
    return {
        "ok": True,
        "id": row_id,
        "friendly_name": friendly,
    }


@app.delete("/api/me/webauthn/{credential_row_id}")
async def api_me_webauthn_delete(
    credential_row_id: int,
    user: CurrentUser,
):
    """Revoke ONE passkey owned by the caller.

    The DB delete is gated on ``(user_id, id)`` so passing another
    user's credential id 404s instead of revoking it.
    """
    _webauthn_self_guard(user)
    with db_conn() as c:
        ok = auth.delete_user_credential(c, user.id, credential_row_id)
        if ok:
            try:
                _ops_mod.write_admin_audit(
                    c, "passkey_self_delete",
                    target_kind="auth", target_name=user.username,
                    target_id=str(credential_row_id),
                    actor=user.username,
                    message=f"passkey id={credential_row_id} revoked by user {user.username}",
                )
            except Exception as e:
                print(f"[webauthn] self-delete audit-row write failed: {e}")
    if not ok:
        raise HTTPException(status_code=404, detail="Passkey not found.")
    print(f"[webauthn] {user.username} revoked passkey id={credential_row_id}")
    return {"ok": True}


# ============================================================================
# Admin: user / session / API-token management (step 5).
# ============================================================================
class UserCreate(BaseModel):
    username: str
    role: str  # "admin" | "readonly"
    auth_source: str = "local"  # "local" | "authentik"
    password: Optional[str] = None  # required when auth_source == "local"
    email: Optional[str] = None


class UserPatch(BaseModel):
    role: Optional[str] = None
    disabled: Optional[bool] = None


class PasswordResetIn(BaseModel):
    new_password: str


class TokenCreate(BaseModel):
    name: str
    role: str  # "admin" | "readonly"


@app.get("/api/users")
async def api_list_users(_admin: AdminUser):
    """Return every user row (admin-only)."""
    with db_conn() as c:
        return {"users": auth.list_users(c)}


@app.post("/api/users")
async def api_create_user(
    u: UserCreate,
    _admin: AdminUser,
):
    """Create a new user with the supplied role + password."""
    name = (u.username or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Username is required.")
    if u.role not in ("admin", "readonly"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'readonly'.")
    if u.auth_source not in ("local", "authentik"):
        raise HTTPException(status_code=400, detail="auth_source must be 'local' or 'authentik'.")
    if u.auth_source == "local":
        if not u.password or len(u.password) < 8:
            raise HTTPException(status_code=400, detail="Local users need a password with 8+ characters.")
    with db_conn() as c:
        if auth.get_user_by_username(c, name):
            raise HTTPException(status_code=409, detail="That username is already taken.")
        user = auth.create_user(
            c, name, u.email or None,
            u.password if u.auth_source == "local" else None,
            u.role, u.auth_source,
        )
        _ops_mod.write_admin_audit(
            c, "user_create",
            target_kind="user", target_name=user.username, target_id=str(user.id),
            actor=_admin.username,
            message=f"Created {u.auth_source} user '{user.username}' with role '{u.role}'",
        )
    return {"ok": True, "id": user.id, "username": user.username, "role": user.role}


@app.patch("/api/users/{user_id}")
async def api_update_user(
    user_id: int,
    p: UserPatch,
    admin: AdminUser,
):
    """Patch one user's mutable fields (role / disabled / display name)."""
    with db_conn() as c:
        target = auth.get_user(c, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        if p.role is not None and p.role not in ("admin", "readonly"):
            raise HTTPException(status_code=400, detail="Role must be 'admin' or 'readonly'.")
        # Guard: can't demote or disable the last active admin — that
        # would lock everyone out of admin functions.
        new_role = p.role if p.role is not None else target.role
        new_disabled = p.disabled if p.disabled is not None else target.disabled
        losing_admin = target.role == "admin" and not target.disabled and (
            new_role != "admin" or new_disabled
        )
        if losing_admin and auth.count_active_admins(c) <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot demote or disable the last active admin.",
            )
        changes = []
        if p.role is not None:
            auth.set_user_role(c, user_id, p.role)
            changes.append(f"role -> {p.role}")
        if p.disabled is not None:
            auth.set_user_disabled(c, user_id, bool(p.disabled))
            changes.append(f"disabled -> {bool(p.disabled)}")
        if changes:
            _ops_mod.write_admin_audit(
                c, "user_update",
                target_kind="user", target_name=target.username, target_id=str(user_id),
                actor=admin.username,
                message=f"Updated user '{target.username}': {', '.join(changes)}",
            )
    return {"ok": True}


@app.delete("/api/users/{user_id}")
async def api_delete_user(
    user_id: int,
    admin: AdminUser,
):
    """Delete a user by id — refuses to delete self or the last active admin."""
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="You can't delete yourself.")
    with db_conn() as c:
        target = auth.get_user(c, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        if target.role == "admin" and not target.disabled and auth.count_active_admins(c) <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete the last active admin.",
            )
        # Capture the avatar path BEFORE the delete so we can unlink
        # the file on disk afterwards. Without this the file lingers
        # under /app/data/avatars/ and a recycled user-id (rare —
        # autoincrement reset / restore-from-backup) would silently
        # inherit the orphan. in the code review.
        profile = auth.get_user_profile(c, user_id) or {}
        avatar_path = (profile.get("avatar_path") or "").strip()
        target_username = target.username
        auth.delete_user(c, user_id)
        _ops_mod.write_admin_audit(
            c, "user_delete",
            target_kind="user", target_name=target_username, target_id=str(user_id),
            actor=admin.username,
            message=f"Deleted user '{target_username}' (id={user_id})",
        )
    if avatar_path:
        try:
            full = os.path.join(_AVATAR_DIR, avatar_path)
            if os.path.exists(full):
                os.remove(full)
        except OSError:
            pass  # best-effort cleanup; the orphan is cosmetic
    return {"ok": True}


@app.post("/api/users/{user_id}/reset-password")
async def api_reset_password(
    user_id: int,
    r: PasswordResetIn,
    admin: AdminUser,
):
    """Admin password-reset for a local user.

    Note: this ALSO clears any TOTP enrolment. Operators reset
    passwords when a user has lost access; that usually means their
    authenticator device is gone too. The user re-enrols via Profile
    after the next login if 2FA is still required by policy.
    """
    if not r.new_password or len(r.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be 8+ characters.")
    with db_conn() as c:
        target = auth.get_user(c, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        if target.auth_source != "local":
            raise HTTPException(
                status_code=400,
                detail="Authentik-managed users must change their password in Authentik.",
            )
        auth.admin_reset_password(c, user_id, r.new_password)
        _ops_mod.write_admin_audit(
            c, "user_pw_reset",
            target_kind="user", target_name=target.username, target_id=str(user_id),
            actor=admin.username,
            message=f"Admin reset password for '{target.username}' (also clears TOTP)",
        )
    return {"ok": True}


@app.post("/api/users/{user_id}/disable-totp")
async def api_admin_disable_totp(
    user_id: int,
    _request: Request,
    admin: AdminUser,
):
    """Admin override: clear a user's TOTP enrolment + lockout state.

    Useful when a user has lost their authenticator device. The user
    re-enrols via Profile on the next login if policy still requires
    2FA for their role. Audited via the history table with
    op_type='totp_admin_disabled'.
    """
    with db_conn() as c:
        target = auth.get_user(c, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        if target.auth_source != "local":
            raise HTTPException(
                status_code=400,
                detail="Authentik users manage 2FA in their IdP.",
            )
        state = auth.get_user_totp_state(c, user_id)
        if not state["enabled"]:
            return {"ok": True, "already_disabled": True}
        auth.clear_user_totp(c, user_id)
        # Audit row -- mirrors the ssh_run pattern above.
        try:
            # `write_admin_audit` calls `assert_op_type` internally and
            # uses the same column shape so the audit row lands
            # identically to the previous direct-INSERT.
            _ops_mod.write_admin_audit(
                c, "totp_admin_disabled",
                target_kind="auth",
                target_name=target.username, target_id=str(user_id),
                actor=admin.username,
                message=f"2FA disabled for {target.username} by {admin.username}",
            )
        except Exception as e:
            # Defensive log + continue is correct (don't roll back the
            # credential change just because the audit row failed), but
            # a silent `print` to stderr meant the operator looking at
            # History saw no record of the change. Escalate to a
            # notification so the operator sees the missing audit trail
            # in-app + Apprise. The credential change ITSELF persisted
            # via `auth.disable_totp` at line ~16266 — the notification
            # carries the disabled-target + the SQL failure detail.
            print(f"[totp] audit-log insert failed: {e}")
            try:
                from logic.ops import notify as _notify
                await _notify(
                    f"⚠ TOTP audit-row missing for {target.username}",
                    f"2FA was disabled for {target.username} by {admin.username}, "
                    f"but the History audit-row INSERT failed: {e!r}. "
                    f"The credential change DID persist; only the audit "
                    f"trail is missing.",
                    "warning",
                    event="totp_audit_log_failed",
                    actor_username=admin.username,
                    target_kind="auth",
                    target_id=str(user_id),
                )
            except Exception as _nerr:  # noqa: BLE001
                print(f"[totp] audit-failure notification ALSO failed: {_nerr}")
    print(f"[totp] {target.username} disabled BY ADMIN ({admin.username})")
    return {"ok": True}


class TotpForceIn(BaseModel):
    force: bool


@app.post("/api/users/{user_id}/totp-force")
async def api_admin_totp_force(
    user_id: int,
    body: TotpForceIn,
    admin: AdminUser,
):
    """Admin override: per-user force-2FA flag.

    Layers ON TOP of the global totp_required_for_admins / _users
    policy — flipping this ON forces 2FA for THIS user even when
    the global policy doesn't require it for their role. Forcing
    OFF reverts to whatever the global policy says (if global policy
    requires 2FA for the role, the user still has to use it).

    Forcing 2FA on a user who hasn't enrolled yet causes their next
    login to land in the forced-enrolment QR flow — already handled
    by api_local_login's multi-step path.

    Audited via the history table with op_type='totp_force_set'.
    """
    with db_conn() as c:
        target = auth.get_user(c, user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        if target.auth_source != "local":
            raise HTTPException(
                status_code=400,
                detail="Authentik users manage 2FA in their IdP.",
            )
        if bool(target.totp_force_required) == bool(body.force):
            return {"ok": True, "force_required": bool(body.force), "no_change": True}
        auth.set_user_totp_force_required(c, user_id, bool(body.force))
        try:
            _ops_mod.write_admin_audit(
                c, "totp_force_set",
                target_kind="auth",
                target_name=target.username, target_id=str(user_id),
                actor=admin.username,
                message=(f"2FA force-required {'enabled' if body.force else 'cleared'} "
                         f"for {target.username} by {admin.username}"),
            )
        except Exception as e:
            # Same escalation as totp_admin_disabled — surface the
            # audit-row failure to the operator via in-app notification
            # so they know the History trail is missing for this
            # admin action even though the credential change itself
            # persisted.
            print(f"[totp] audit-log insert failed: {e}")
            try:
                from logic.ops import notify as _notify
                await _notify(
                    f"⚠ TOTP force-set audit-row missing for {target.username}",
                    f"TOTP force-required was {'enabled' if body.force else 'cleared'} "
                    f"for {target.username} by {admin.username}, but the History "
                    f"audit-row INSERT failed: {e!r}. The flag DID persist; "
                    f"only the audit trail is missing.",
                    "warning",
                    event="totp_audit_log_failed",
                    actor_username=admin.username,
                    target_kind="auth",
                    target_id=str(user_id),
                )
            except Exception as _nerr:  # noqa: BLE001
                print(f"[totp] audit-failure notification ALSO failed: {_nerr}")
    print(
        f"[totp] {target.username} force-2FA "
        f"{'ENABLED' if body.force else 'CLEARED'} BY ADMIN ({admin.username})"
    )
    return {"ok": True, "force_required": bool(body.force)}


@app.get("/api/sessions")
async def api_list_sessions(_admin: AdminUser):
    """Return every active session across every user (admin-only)."""
    with db_conn() as c:
        return {"sessions": auth.list_sessions(c)}


@app.delete("/api/sessions/{token_id}")
async def api_revoke_session(
    token_id: str,
    admin: AdminUser,
):
    """Revoke one session by token-id (admin-only)."""
    with db_conn() as c:
        auth.delete_session(c, token_id)
        _ops_mod.write_admin_audit(
            c, "session_revoke",
            target_kind="session", target_name=token_id, target_id=token_id,
            actor=admin.username,
            message=f"Revoked session token {token_id}",
        )
    return {"ok": True}


@app.get("/api/tokens")
async def api_list_tokens(_admin: AdminUser):
    """List every API token (raw value never shown — hash-only at rest)."""
    with db_conn() as c:
        return {"tokens": auth.list_api_tokens(c)}


@app.post("/api/tokens")
async def api_create_token(
    t: TokenCreate,
    admin: AdminUser,
):
    """Mint a new API token. The raw token is returned EXACTLY ONCE on create."""
    name = (t.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required.")
    if t.role not in ("admin", "readonly"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'readonly'.")
    try:
        with db_conn() as c:
            raw = auth.create_api_token(c, name, t.role, admin.id)
            _ops_mod.write_admin_audit(
                c, "token_create",
                target_kind="api_token", target_name=name, target_id=name,
                actor=admin.username,
                message=f"Created API token '{name}' with role '{t.role}'",
            )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="A token with that name already exists.")
    # Raw token returned ONCE. UI shows a one-time reveal modal; we store
    # only the SHA-256 hash. If lost, the operator must rotate.
    return {"ok": True, "name": name, "role": t.role, "token": raw}


@app.delete("/api/tokens/{token_id}")
async def api_delete_token(
    token_id: int,
    admin: AdminUser,
):
    """Revoke an API token by id (idempotent — 404 is success)."""
    with db_conn() as c:
        auth.delete_api_token(c, token_id)
        _ops_mod.write_admin_audit(
            c, "token_revoke",
            target_kind="api_token", target_name=str(token_id), target_id=str(token_id),
            actor=admin.username,
            message=f"Revoked API token id={token_id}",
        )
    return {"ok": True}


# ============================================================================
# Backups — zip containing the full SQLite DB + avatars directory.
# Admin-only; list/create/download/delete/restore. See logic/backups.py for
# the safety dance (consistent .backup() snapshot, pre-restore auto-snapshot,
# path-traversal guards).
# ============================================================================
@app.get("/api/backups")
async def api_list_backups(_admin: AdminUser):
    """List every SQLite + avatars snapshot in the backups directory."""
    return {"backups": backups.list_backups()}


@app.post("/api/backups")
async def api_create_backup(admin: AdminUser):
    """Create a new SQLite + avatars snapshot via SQLite's online .backup() API."""
    result = backups.create_backup()
    # Retention — surfaced to the operator in the response so they can
    # see what got pruned without re-listing. Zero/empty setting means
    # "keep all", which is the safe default for a fresh install.
    # `backup_retention_count` is now a TUNABLE (DB > env > default
    # with bounds clamp); legacy plain-settings row still hydrates
    # the form for parity.
    try:
        keep = tuning.tuning_int(Tunable.BACKUP_RETENTION_COUNT)
    except (TypeError, ValueError):
        keep = 0
    pruned = backups.prune_backups(keep) if keep > 0 else []
    if pruned:
        result = {**result, "pruned": pruned}
    backup_name = str((result or {}).get("name", "") or "")
    with db_conn() as c:
        _ops_mod.write_admin_audit(
            c, "backup_create",
            target_kind="backup", target_name=backup_name, target_id=backup_name,
            actor=admin.username,
            message=f"Created backup '{backup_name}'" + (f" (pruned {len(pruned)})" if pruned else ""),
        )
    return result


@app.get("/api/backups/{name}")
async def api_download_backup(
    name: str, _admin: AdminUser,
):
    """Stream a named backup zip to the operator."""
    try:
        path = backups.backup_path(name)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid backup name")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, filename=name, media_type="application/zip")


@app.delete("/api/backups/{name}")
async def api_delete_backup(
    name: str, admin: AdminUser,
):
    """Delete a named backup file (idempotent — already-gone is success)."""
    try:
        backups.delete_backup(name)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid backup name")
    with db_conn() as c:
        _ops_mod.write_admin_audit(
            c, "backup_delete",
            target_kind="backup", target_name=name, target_id=name,
            actor=admin.username,
            message=f"Deleted backup '{name}'",
        )
    return {"ok": True}


@app.post("/api/backups/{name}/restore")
async def api_restore_backup_named(
    name: str, admin: AdminUser,
):
    """Restore the named backup over the live DB (audit-row written first)."""
    try:
        result = backups.restore_by_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore failed: {e}")
    with db_conn() as c:
        _ops_mod.write_admin_audit(
            c, "backup_restore",
            target_kind="backup", target_name=name, target_id=name,
            actor=admin.username,
            message=f"Restored backup '{name}'",
        )
    return result


@app.post("/api/backups/restore")
async def api_restore_backup_upload(
    request: Request, _admin: AdminUser,
):
    """Upload a zip file and restore from it. 200 MB cap."""
    form = await request.form()
    file_field = form.get("file")
    if file_field is None or isinstance(file_field, str) or not hasattr(file_field, "read"):
        raise HTTPException(status_code=400, detail="Field 'file' missing")
    file = file_field  # type: ignore[assignment]  # narrowed via isinstance + hasattr guard
    data = await file.read()
    if len(data) > backups.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Upload too large (max {backups.MAX_UPLOAD_BYTES // 1_000_000} MB)",
        )
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty upload")
    # Persist the uploaded zip to a temp file on the data volume so the
    # restore function (which expects a filesystem path) can work on it.
    with tempfile.NamedTemporaryFile(
        delete=False, suffix=".zip",
        dir=os.path.dirname(DB_PATH) or ".",
    ) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        result = backups.restore_from_file(tmp_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid backup: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore failed: {e}")
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    # noinspection PyUnboundLocalVariable
    return result  # `result` is bound iff neither except branch fired (both raise — terminal).


# ============================================================================
# Settings-as-Code — export / import the operator-tunable admin config as
# a single human-readable JSON document. See `logic/config_export.py` for
# the snapshot shape, secret-redaction contract, and apply semantics.
# Admin-only — every endpoint gates on require_admin.
# ============================================================================


@app.get("/api/admin/config-backup/export")
async def api_config_backup_export(_admin: AdminUser):
    """Build a fresh snapshot and stream it as a JSON download.

    Operators commit the file to a private git repo for change tracking.
    Secrets (api keys / passwords / tokens / private keys) are redacted
    to the literal sentinel string `"__OMITTED__"`; on import those
    entries are skipped so the live DB's secret material is preserved.
    """
    snap = config_export.build_snapshot()
    blob = json.dumps(snap, indent=2, sort_keys=True)
    ts = time.strftime("%Y.%m.%d_%H.%M.%S", time.localtime())
    fname = f"omnigrid-config_{ts}.json"
    return Response(
        content=blob,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/admin/config-backup/preview")
async def api_config_backup_preview(_admin: AdminUser):
    """Return the current snapshot as a JSON object (NOT a download).

    Used by the Admin → Config backup tab to show the operator what
    they're about to download / commit / restore. Same shape as the
    download endpoint; just no Content-Disposition header.
    """
    return config_export.build_snapshot()


class ConfigBackupImportIn(BaseModel):
    """Body for the import endpoint — single `payload` field carries
    the full snapshot dict the operator uploaded. Pydantic accepts
    arbitrary nested JSON via `dict`."""
    payload: dict


@app.post("/api/admin/config-backup/import")
async def api_config_backup_import(
    body: ConfigBackupImportIn,
    admin: AdminUser,
):
    """Apply an uploaded snapshot to the live DB. See
    `logic.config_export.apply_snapshot` for the per-surface semantics
    (settings: per-key UPSERT skipping redacted; schedules + ai_memory:
    replace-all).

    Returns the apply-result counters + warnings so the operator's
    toast can summarise what changed.
    """
    try:
        result = config_export.apply_snapshot(body.payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    with db_conn() as c:
        _ops_mod.write_admin_audit(
            c, "config_backup_import",
            target_kind="config_backup",
            actor=admin.username,
            message=f"Imported config-backup snapshot ({len(result.get('warnings') or [])} warning(s))",
        )
    return result


@app.get("/api/admin/config-backup/list")
async def api_config_backup_list(_admin: AdminUser):
    """List saved snapshot files written by the `config_backup`
    schedule kind (or any future manual save-to-disk path)."""
    return {"files": config_export.list_snapshots()}


@app.post("/api/admin/config-backup/save")
async def api_config_backup_save(admin: AdminUser):
    """Write a fresh snapshot to disk on demand. Same path the
    `config_backup` schedule kind uses. Returns the saved file's
    {name, size, mtime}."""
    result = config_export.save_snapshot_to_disk()
    fname = (result or {}).get("name", "") or ""
    with db_conn() as c:
        _ops_mod.write_admin_audit(
            c, "config_backup_save",
            target_kind="config_backup", target_name=fname, target_id=fname,
            actor=admin.username,
            message=f"Saved config-backup snapshot to disk: '{fname}'",
        )
    return result


@app.get("/api/admin/config-backup/saved/{name}")
async def api_config_backup_download_saved(
    name: str, _admin: AdminUser,
):
    """Download a previously-saved snapshot file."""
    try:
        full = config_export.safe_path(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(full, media_type="application/json", filename=name)


@app.post("/api/admin/config-backup/saved/{name}/restore")
async def api_config_backup_restore_saved(
    name: str, admin: AdminUser,
):
    """Read a saved snapshot file and apply it. Same as POSTing the
    file's contents to `/api/admin/config-backup/import`, just routed
    through the disk path so the operator doesn't have to re-upload."""
    try:
        snap = config_export.read_snapshot(name)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        result = config_export.apply_snapshot(snap)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    with db_conn() as c:
        _ops_mod.write_admin_audit(
            c, "config_backup_restore",
            target_kind="config_backup", target_name=name, target_id=name,
            actor=admin.username,
            message=f"Restored config-backup snapshot '{name}' from disk",
        )
    return result


@app.delete("/api/admin/config-backup/saved/{name}")
async def api_config_backup_delete_saved(
    name: str, admin: AdminUser,
):
    """Delete a saved snapshot file."""
    try:
        config_export.delete_snapshot(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    with db_conn() as c:
        _ops_mod.write_admin_audit(
            c, "config_backup_delete",
            target_kind="config_backup", target_name=name, target_id=name,
            actor=admin.username,
            message=f"Deleted config-backup snapshot '{name}'",
        )
    return {"ok": True}


# ============================================================================
# Scheduler — admin-defined recurring jobs. See logic/schedules.py for the
# tick loop + kind registry. Admin-only CRUD; POST .../run fires manually.
# ============================================================================
class ScheduleIn(BaseModel):
    name: str
    kind: str
    params: Optional[dict] = None
    interval_seconds: int
    enabled: bool = True
    # Cadence bundle — cadence_mode picks which of the fields below the
    # tick loop consults. See logic.schedules.CADENCE_MODES.
    cadence_mode: str = "interval"
    run_at_hhmm: Optional[str] = None  # daily/weekly/monthly anchor
    days_of_week: Optional[list[int]] = None  # weekly, Mon=0..Sun=6
    day_of_month: Optional[int] = None  # monthly, 1..31 clamped to EOM


class SchedulePatch(BaseModel):
    name: Optional[str] = None
    kind: Optional[str] = None
    params: Optional[dict] = None
    interval_seconds: Optional[int] = None
    enabled: Optional[bool] = None
    cadence_mode: Optional[str] = None
    # For these three, None in the wire payload means "don't touch";
    # explicit empty ("" / []) means "clear" — handled by
    # schedules.update_schedule().
    run_at_hhmm: Optional[str] = None
    days_of_week: Optional[list[int]] = None
    day_of_month: Optional[int] = None


@app.get("/api/schedules")
async def api_list_schedules(_admin: AdminUser):
    """Return every schedule row + its next-fire timestamp."""
    with db_conn() as c:
        return {
            "schedules": schedules.list_schedules(c),
            "kinds": sorted(schedules.SCHEDULE_KINDS.keys()),
            "min_interval_seconds": schedules.MIN_INTERVAL_SECONDS,
        }


@app.post("/api/schedules")
async def api_create_schedule(
    s: ScheduleIn,
    admin: AdminUser,
):
    """Create a new schedule row (validates kind + cron / interval expression)."""
    name = (s.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required.")
    if s.kind not in schedules.SCHEDULE_KINDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown schedule kind '{s.kind}'. "
                f"Known: {', '.join(sorted(schedules.SCHEDULE_KINDS.keys()))}"
            ),
        )
    if s.interval_seconds < schedules.MIN_INTERVAL_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"interval_seconds must be >= {schedules.MIN_INTERVAL_SECONDS}"
            ),
        )
    params = s.params or {}
    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="params must be a JSON object.")
    try:
        with db_conn() as c:
            row = schedules.create_schedule(
                c, name, s.kind, params, int(s.interval_seconds),
                bool(s.enabled),
                run_at_hhmm=s.run_at_hhmm,
                cadence_mode=s.cadence_mode or "interval",
                days_of_week=s.days_of_week,
                day_of_month=s.day_of_month,
            )
            _ops_mod.write_admin_audit(
                c, "schedule_create",
                target_kind="schedule", target_name=name, target_id=str(row.get("id") or ""),
                actor=admin.username,
                message=f"Created schedule '{name}' (kind={s.kind}, interval={s.interval_seconds}s)",
            )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="A schedule with that name already exists.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "schedule": row}


@app.patch("/api/schedules/{schedule_id}")
async def api_update_schedule(
    schedule_id: int,
    p: SchedulePatch,
    admin: AdminUser,
):
    """Patch one schedule's mutable fields by id."""
    # exclude_unset keeps explicit None values so "clear this field" works
    # via wire-level null (e.g. flipping back to interval mode by sending
    # {cadence_mode:"interval", run_at_hhmm:null, days_of_week:null,
    # day_of_month:null}). update_schedule() knows which fields are
    # clearable-on-None; the rest still ignore None as before.
    patch_fields = p.model_dump(exclude_unset=True)
    if "name" in patch_fields and patch_fields["name"] is not None:
        patch_fields["name"] = patch_fields["name"].strip()
        if not patch_fields["name"]:
            raise HTTPException(status_code=400, detail="Name cannot be blank.")
    try:
        with db_conn() as c:
            existing = schedules.get_schedule(c, schedule_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Schedule not found.")
            row = schedules.update_schedule(c, schedule_id, **patch_fields)
            sched_name = (row or {}).get("name") or existing.get("name") or str(schedule_id)
            _ops_mod.write_admin_audit(
                c, "schedule_update",
                target_kind="schedule", target_name=sched_name, target_id=str(schedule_id),
                actor=admin.username,
                message=f"Updated schedule '{sched_name}': {', '.join(sorted(patch_fields.keys()))}",
            )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="A schedule with that name already exists.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "schedule": row}


@app.delete("/api/schedules/{schedule_id}")
async def api_delete_schedule(
    schedule_id: int,
    admin: AdminUser,
):
    """Delete a schedule by id (idempotent — already-gone is success)."""
    with db_conn() as c:
        existing = schedules.get_schedule(c, schedule_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Schedule not found.")
        sched_name = existing.get("name") or str(schedule_id)
        schedules.delete_schedule(c, schedule_id)
        _ops_mod.write_admin_audit(
            c, "schedule_delete",
            target_kind="schedule", target_name=sched_name, target_id=str(schedule_id),
            actor=admin.username,
            message=f"Deleted schedule '{sched_name}' (kind={existing.get('kind') or 'unknown'})",
        )
    return {"ok": True}


@app.post("/api/schedules/{schedule_id}/run")
async def api_run_schedule(
    schedule_id: int,
    admin: AdminUser,
):
    """Fire a schedule immediately, bypassing its interval.

    Uses the same kind-callable path as the tick loop, so the resulting
    op flows through ops.py exactly as if the schedule had been due.
    Returns the op id so the UI can deep-link the ops panel.
    """
    with db_conn() as c:
        s = schedules.get_schedule(c, schedule_id)
    if not s:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    try:
        op_id = await schedules.fire_schedule(s)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fire failed: {e}")
    sched_name = s.get("name") or str(schedule_id)
    with db_conn() as c:
        _ops_mod.write_admin_audit(
            c, "schedule_run_now",
            target_kind="schedule", target_name=sched_name, target_id=str(schedule_id),
            actor=admin.username,
            message=f"Manually fired schedule '{sched_name}' (op_id={op_id or 'unknown'})",
        )
    return {"ok": True, "op_id": op_id}


@app.get("/api/schedules/queue")
async def api_schedule_queue(
    limit: int = 50,
    page: int = 1,
    page_size: int = 0,
    search: str = "",
    *,
    _admin: AdminUser,
):
    """Recent scheduler-driven ops from the history table.

    Filtered to ``actor='scheduler'`` so user-triggered runs of the
    same op types don't clutter the view.

    Pagination contract: when ``page_size`` is passed the response
    returns ONE page of rows plus `total` / `page` / `page_size` so
    the UI can render "Page N of M" without double-fetching. When
    ``page_size`` is 0 (or omitted), the endpoint falls back to the
    legacy flat-list shape (`limit` rows, no `total`) so older
    clients keep working.

    Optional ``search`` param does a case-insensitive substring
    match on ``target_name`` / ``op_type`` / ``status``. Backend
    filtering keeps the page count accurate when the operator is
    searching across thousands of rows.
    """
    # Build a reusable WHERE-clause + bind args. Backend search lives
    # entirely in SQL so the page count + slice are correct against
    # the filtered set, not the unfiltered total.
    actor = schedules.SCHEDULER_ACTOR
    where = "actor = ?"
    args: list = [actor]
    s = (search or "").strip().lower()
    if s:
        where += (" AND ("
                  "LOWER(COALESCE(target_name, '')) LIKE ? OR "
                  "LOWER(COALESCE(op_type, '')) LIKE ? OR "
                  "LOWER(COALESCE(status, '')) LIKE ?"
                  ")")
        like = f"%{s}%"
        args.extend([like, like, like])

    # Legacy single-query path — keep until every caller is migrated.
    if page_size <= 0:
        limit = max(1, min(int(limit), 500))
        with db_conn() as c:
            rows = c.execute(
                f"SELECT * FROM history WHERE {where} "
                f"ORDER BY ts DESC LIMIT ?",
                args + [limit],
            ).fetchall()
        return {"queue": [dict(r) for r in rows]}

    # Paginated path — count + slice. Cap page_size at 100 to guard
    # against accidentally-huge queries.
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    offset = (page - 1) * page_size
    with db_conn() as c:
        total_row = c.execute(
            f"SELECT COUNT(*) FROM history WHERE {where}", args,
        ).fetchone()
        total = int((total_row[0] if total_row else 0) or 0)
        rows = c.execute(
            f"SELECT * FROM history WHERE {where} "
            f"ORDER BY ts DESC LIMIT ? OFFSET ?",
            args + [page_size, offset],
        ).fetchall()
    return {
        "queue": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "search": search or "",
    }


# Login HTML page. Served as a discrete route (not via StaticFiles) because
# /login has no trailing slash and we want it to map to static/login.html
# directly without relying on html=True directory-index behaviour. Also
# listed in auth.FULLY_PUBLIC_PREFIXES so the middleware never gates it.
@app.get("/login")
async def login_page():
    """Serve the login HTML shell (anonymous; redirects already-authed users)."""
    return _render_shell("static/login.html")


# UI icon sprite. Served as a discrete route (not via the catch-all
# StaticFiles mount) so we can attach a long-cache header — every
# `<use href="/img/ui-sprite.svg?v=__APP_VERSION__#icon-..."/>` site
# is version-busted by the shell renderer at request time, so the URL
# itself changes on every PATCH bump. With `immutable` + a one-year
# max-age the browser parks a single sprite copy across navigations
# (no per-page revalidation round-trip) and the `?v=...` change forces
# a fresh fetch the next time the SPA shell ships a new version.
# Registered BEFORE the StaticFiles "/" mount per CLAUDE.md mount-order
# rule.
@app.get("/img/ui-sprite.svg")
async def serve_ui_sprite():
    """Serve the SVG sprite that ships every Lucide icon used by the SPA."""
    path = "static/img/ui-sprite.svg"
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="UI sprite not found")
    return FileResponse(
        path,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


# Shell-HTML cache — tiny map keyed by file path. Each entry stores the
# assembled file bytes (with `<!-- INCLUDE: ... -->` markers expanded) and
# the combined mtime tuple of the master file + every referenced partial;
# a disk change to ANY of them invalidates the entry lazily on the next
# request. `str.replace` runs on every hit (cheap — the two HTMLs together
# are <200 KB) so `__APP_VERSION__` marker references pick up a new PATCH
# as soon as VERSION.txt changes, without any restart.
_SHELL_CACHE: dict = {}

# Partial-include marker. Matches `<!-- INCLUDE: <path> -->` with
# arbitrary leading whitespace preserved (via the `.sub` callback below
# that re-emits the original indent). The path is resolved relative to
# `static/_partials/` and a path-traversal guard refuses anything that
# would escape the partials root. One level of inlining only — partials
# don't recursively include each other today (keeps the contract simple
# and the cache-key audit shallow).
_INCLUDE_RE = re.compile(r"<!--\s*INCLUDE:\s*(?P<rel>\S+)\s*-->")
_PARTIALS_BASE = os.path.join("static", "_partials")


def _expand_includes(body: str, path: str) -> tuple[str, tuple]:
    """Expand `<!-- INCLUDE: <rel-path> -->` markers in `body`.

    Returns `(assembled_body, mtime_signature)` where `mtime_signature`
    is a tuple of `(master_mtime_ns, *(partial_path, partial_mtime_ns)...)`
    that the caller uses as the cache key. Any partial that fails to
    read collapses to an empty string in the output (visible visual
    regression but the page still renders) and contributes its
    attempted-mtime to the signature so the next disk change invalidates.

    Multi-pass: an included partial can ITSELF carry INCLUDE markers
    pointing at other partials (e.g. an admin sub-tab template
    embedding the shared `_components/og-range-picker.html`). The
    expander iterates until the body stabilises with no remaining
    markers OR `_MAX_INCLUDE_DEPTH` is reached (safety net against a
    pathological self-referential include loop — collapses any
    still-unresolved markers to empty strings rather than spinning).
    """
    base = os.path.abspath(_PARTIALS_BASE)
    sig: list = []
    try:
        sig.append(os.stat(path).st_mtime_ns)
    except OSError:
        sig.append(0)

    def _replace(m: "re.Match[str]") -> str:
        rel = m.group("rel")
        candidate = os.path.abspath(os.path.join(_PARTIALS_BASE, rel))
        # Path-traversal guard: refuse anything that escapes _partials/.
        if candidate != base and not candidate.startswith(base + os.sep):
            sig.append((rel, 0))
            return ""
        try:
            mt = os.stat(candidate).st_mtime_ns
            with open(candidate, encoding="utf-8") as f:
                content = f.read()
        except OSError:
            sig.append((rel, 0))
            return ""
        sig.append((rel, mt))
        return content

    _MAX_INCLUDE_DEPTH = 8
    expanded = body
    for _depth in range(_MAX_INCLUDE_DEPTH):
        if not _INCLUDE_RE.search(expanded):
            break
        expanded = _INCLUDE_RE.sub(_replace, expanded)
    else:
        # Hit the depth cap with markers still unresolved — strip any
        # remaining markers so they don't render as literal HTML comments
        # in the operator's browser. Diagnostic print so a future
        # contributor sees the loop in Admin → Logs instead of a silent
        # truncation.
        if _INCLUDE_RE.search(expanded):
            print(
                f"[_expand_includes] WARN: include depth {_MAX_INCLUDE_DEPTH} "
                f"exceeded for {path!r} — remaining markers stripped; "
                f"check for a self-referential INCLUDE loop."
            )
            expanded = _INCLUDE_RE.sub("", expanded)
    return expanded, tuple(sig)


# noinspection PyTypeChecker,PyUnresolvedReferences
def _render_shell(path: str) -> Response:
    """Serve an HTML shell with `__APP_VERSION__` → current version.

    Used for `/` and `/login` — both reference external JS/CSS as
    `src="/js/app.js?v=__APP_VERSION__"`, and this is the substitution
    point that turns that literal into an actual cache-bustable URL.
    Any other entry-point HTML that references versioned assets should
    be served through this too; the bare StaticFiles mount at "/" won't
    run the substitution.

    Also expands `<!-- INCLUDE: admin/<tab>.html -->` markers so the
    admin sub-tabs can live in `static/_partials/admin/` instead of one
    14k-line `index.html`. Cache key tracks every partial's mtime so a
    partial edit is picked up on the next request without restart.
    """
    try:
        master_mtime = os.stat(path).st_mtime_ns
    except OSError:
        raise HTTPException(status_code=404, detail=f"{path} not found")
    cached = _SHELL_CACHE.get(path)
    # Pre-bind `body` so the linter can prove it's always assigned. The
    # control-flow below sets it in BOTH branches (cache hit + cache
    # miss), but type-checkers can't trace through the `cached = None`
    # reassignment that bridges the two; the empty initial value is
    # never observed at runtime because the substitution call always
    # follows one of the two write paths.
    body: str = ""
    # Quick path: cached entry's signature still matches every disk file
    # we depend on. The master mtime alone isn't enough — a partial edit
    # leaves the master untouched so we re-walk the partial mtimes too.
    if cached is not None and cached[0][0] == master_mtime:
        # Re-stat every partial referenced by the cached signature; if
        # they all match, serve from cache. Cheap: ~18 stat() calls for
        # the admin partials, each <1 µs.
        ok = True
        for entry in cached[0][1:]:
            rel, prev_mt = entry
            cand = os.path.abspath(os.path.join(_PARTIALS_BASE, rel))
            try:
                if os.stat(cand).st_mtime_ns != prev_mt:
                    ok = False
                    break
            except OSError:
                if prev_mt != 0:
                    ok = False
                    break
        if ok:
            body = cached[1]
        else:
            cached = None
    if cached is None:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        body, sig = _expand_includes(raw, path)
        _SHELL_CACHE[path] = (sig, body)
    # Use the LIVE version, not the import-time snapshot. This lets an
    # operator edit /app/VERSION.txt on the server and have cache-busting
    # URLs follow without restarting the container.
    body = body.replace("__APP_VERSION__", read_version())
    # Cache-Control: no-cache, must-revalidate — the SPA shell is the
    # entry point that references EVERY versioned asset (`/js/app.js?v=...`,
    # `/css/style.css`, the inline `window.__APP_VERSION__` global), so a
    # browser-cached shell would freeze the whole asset chain at a stale
    # PATCH and the `?v=` bust scheme falls apart. `no-cache` doesn't
    # disable caching — it forces revalidation on every navigation so a
    # 304 is allowed when nothing changed; only the body bytes are
    # skipped, the headers (including the freshly-substituted version)
    # are re-served. Safe for the SPA shell; do NOT copy onto static
    # assets (they SHOULD cache by the URL-versioning contract).
    return Response(
        content=body,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


# SPA shell. Served through _render_shell so the version substitution
# applies — StaticFiles at "/" would hand back the raw file with the
# literal "__APP_VERSION__" marker still in the script srcs. Registered
# BEFORE the StaticFiles mount below (mount-order rule applies).
@app.get("/")
async def spa_shell():
    """Serve the SPA master HTML for every non-/api path (catch-all route)."""
    return _render_shell("static/index.html")


# Deep-link routes for every SPA view. The Alpine front-end calls
# `history.replaceState('/nodes')` when you switch tabs so reloading
# a deep link drops you back on the same tab; without a matching
# server route, `GET /nodes` would fall through to the StaticFiles
# mount and 404. The shell itself is identical to `/` — Alpine's
# `_applyRouteFromPath()` picks the view based on `location.pathname`
# once the page boots. Settings / Admin accept a sub-path segment
# (`/settings/oidc`, `/admin/users`) so those deep links work too.
# Strict rule: every entry in `navItems()` (static/js/app.js) must
# have a matching entry here, otherwise a refresh / direct-URL visit
# returns the StaticFiles 404 `{"detail":"Not Found"}`.
_SPA_ROUTES = ("stacks", "services", "nodes", "hosts", "apps", "history")

for _view in _SPA_ROUTES:
    app.add_api_route(f"/{_view}", spa_shell, methods=["GET"])


@app.get("/settings")
@app.get("/settings/{section}")
async def spa_settings_shell(section: str = ""):
    """SPA shell route for /settings and /settings/<section> deep links.
    Section is consumed client-side by `_applyRouteFromPath()`; this
    handler only needs to return the master HTML."""
    _ = section
    return _render_shell("static/index.html")


@app.get("/admin")
@app.get("/admin/{tab}")
async def spa_admin_shell(tab: str = ""):
    """SPA shell route for /admin and /admin/<tab> deep links.
    Tab is consumed client-side; this handler only returns the master
    HTML."""
    _ = tab
    return _render_shell("static/index.html")


@app.get("/stats")
@app.get("/stats/{tab}")
async def spa_stats_shell(tab: str = ""):
    """SPA shell route for /stats and /stats/<tab> deep links.
    Tab is consumed client-side; this handler only returns the master
    HTML."""
    _ = tab
    return _render_shell("static/index.html")


# Prometheus scrape endpoint.
# Implemented as a regular route (not app.mount) because Starlette's
# Mount only matches the mount path WITH a trailing slash — bare GET
# /metrics (what every Prometheus scraper sends by default) falls
# through to the StaticFiles catch-all and returns 404. Using a route
# sidesteps the trailing-slash foot-gun entirely.
@app.get("/metrics")
async def prometheus_metrics():
    """Return the Prometheus exposition format for every registered metric."""
    return Response(
        content=metrics.generate_latest(metrics.REGISTRY),
        media_type=metrics.CONTENT_TYPE_LATEST,
    )


# Serve node_modules directly — but only the specific files that
# index.html / login.html / alpine-gate.js actually reference.
# Earlier this was a wildcard `app.mount("/node_modules", StaticFiles(...))`
# which served EVERY file in the tree (readmes, sourcemaps, TS sources,
# unused locales, package metadata) even though only ~7 files are
# actually requested. A prior code review flagged this as
# unnecessary surface bloat — not a security hole (the files are public
# on npm anyway) but tidy + faster to audit.
#
# Adding a new dep that needs serving = add its path to _NPM_ALLOWED.
# Anything outside the allowlist 404s; anything inside is served
# straight from the on-disk file with the correct media-type.
_NPM_ALLOWED: Set[str] = {
    "@tailwindcss/browser/dist/index.global.js",
    "alpinejs/dist/cdn.min.js",
    "sweetalert2/dist/sweetalert2.all.min.js",
    "@xterm/xterm/css/xterm.css",
    "@xterm/xterm/lib/xterm.js",
    "@xterm/addon-fit/lib/addon-fit.js",
    "@xterm/addon-web-links/lib/addon-web-links.js",
    "qrcode-generator/dist/qrcode.js",
}


# FastAPI `{subpath:path}` route-converter accepts segments with slashes —
# required so a request like `/node_modules/@xterm/xterm/lib/xterm.js`
# binds the whole tail to `subpath`. Registered via ``add_api_route``
# instead of ``@app.get`` so PyCharm's FastAPI inspector doesn't try to
# match the ``{subpath:path}`` converter literal against the function's
# parameter list (it parses the whole literal as a parameter name and
# raises a spurious mismatch warning). Programmatic registration is the
# same FastAPI primitive the decorator builds on top of — no behavioural
# difference, just no inspector confusion.
async def api_node_modules(subpath: str = FastApiPath(...)):
    """Allowlist-gated static server for the 7 npm files the SPA actually
    uses. Everything else returns 404 — keeps the served surface tight.
    """
    # Path-traversal guard: no `..` segments, no leading slashes, must
    # match an entry in the allowlist exactly. Belt-and-braces — FastAPI's
    # path converter wouldn't let `..` through in practice, but the
    # explicit check makes the security property obvious.
    if ".." in subpath or subpath.startswith("/") or subpath not in _NPM_ALLOWED:
        raise HTTPException(404, "Not found")
    # Defence-in-depth: even though `_NPM_ALLOWED` is a closed set of
    # 8 known-safe relative paths, also normalise the joined result
    # via `os.path.realpath` and confirm it stays within the
    # node_modules root. Catches any future relaxation of the
    # allowlist (operator adds a new entry that happens to traverse
    # via a symlink) AND silences static-analysis path-injection
    # findings that won't trust enum-allowlist validation alone.
    # Mirrors the `safe_log_path` pattern in `logic/logs.py`.
    root = os.path.realpath("node_modules")
    file_path = os.path.realpath(os.path.join(root, subpath))
    if file_path != root and not file_path.startswith(root + os.sep):
        raise HTTPException(404, "Not found")
    if not os.path.isfile(file_path):
        raise HTTPException(404, "Not found")
    return FileResponse(file_path)


app.add_api_route(
    "/node_modules/{subpath:path}",
    api_node_modules,
    methods=["GET"],
)

# Translation bundles. Mounted at /i18n/ (before the "/" catch-all, same
# ordering rule as /metrics / /node_modules) so the SPA can fetch
# /i18n/en.json, /i18n/ar.json, /i18n/index.json at boot. Anonymous-
# readable: language files are UI strings, not secrets.
if os.path.isdir("static/i18n"):
    app.mount("/i18n", StaticFiles(directory="static/i18n"), name="i18n")

# SPA JavaScript entry + ES-module siblings.
#
# `static/js/app.js` is now an ES module that imports sibling
# `static/js/app-*.js` files. Each `import` URL inside app.js uses
# `?v=__APP_VERSION__` for cache-busting on deploy. StaticFiles serves
# `.js` files raw, so the literal marker would never get substituted —
# this route does the same `__APP_VERSION__` → live version replacement
# `_render_shell()` does for the HTML shell, scoped to the app.js entry
# point + its sibling modules. The substitution is text-level (cheap,
# no parser), bounded by the closed `_APP_JS_MODULES` set so a typo'd
# module path 404s instead of fishing arbitrary files.
#
# Cache-Control: no-cache, must-revalidate — same shape as the SPA
# shell. The `?v=` query string only changes on deploy, so the browser
# revalidates per-tab-open but a 304 is fine in steady state. The
# underlying file bytes change on every deploy regardless because every
# `__APP_VERSION__` site gets substituted with the current PATCH.
_APP_JS_MODULES: Set[str] = set()


def _refresh_app_js_modules() -> None:
    """Discover every `static/js/app*.js` file at startup.
    The set populates `_APP_JS_MODULES`; the route below allows any
    name in this set. Re-scan on container restart only — adding a new
    module file requires a new deploy (which restarts the process)."""
    _APP_JS_MODULES.clear()
    js_dir = os.path.join("static", "js")
    if not os.path.isdir(js_dir):
        return
    for name in os.listdir(js_dir):
        if name.startswith("app") and name.endswith(".js"):
            _APP_JS_MODULES.add(name)


_refresh_app_js_modules()


async def serve_app_js_module(name: str = FastApiPath(...)):
    """Serve a SPA-side JS module with `__APP_VERSION__` substitution.

    Scope: `static/js/app.js` and `static/js/app-*.js` (the ES-module
    refactor of the SPA's top-level component). Other JS files under
    `static/js/` (i18n.js, auth-fetch.js, alpine-gate.js, login.js)
    are served raw — no module imports to cache-bust, the SPA shell's
    own `?v=__APP_VERSION__` query on each `<script>` tag is sufficient.
    """
    js_dir = os.path.join("static", "js")
    file_path = os.path.realpath(os.path.join(js_dir, name))
    js_root = os.path.realpath(js_dir)
    if file_path != js_root and not file_path.startswith(js_root + os.sep):
        raise HTTPException(404, "Not found")
    if not os.path.isfile(file_path):
        raise HTTPException(404, "Not found")
    if name in _APP_JS_MODULES:
        try:
            with open(file_path, encoding="utf-8") as f:
                body = f.read()
        except OSError:
            raise HTTPException(404, "Not found")
        body = body.replace("__APP_VERSION__", read_version())
        return Response(
            content=body,
            media_type="application/javascript; charset=utf-8",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )
    # Non-app JS files — serve raw via FileResponse so StaticFiles
    # semantics (mtime-based ETag) still work.
    return FileResponse(
        file_path,
        media_type="application/javascript; charset=utf-8",
    )


app.add_api_route("/js/{name}", serve_app_js_module, methods=["GET"])

# Keep this line LAST — StaticFiles at "/" is a catch-all.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
