"""Hosts-view endpoints — `/api/hosts/list` (skeleton),
`/api/hosts/one/{host_id}` (per-host probe + merge),
`/api/hosts/config` (CRUD), `/api/hosts/{id}/resume-sampling`,
`/api/hosts/{id}/provider/{provider}/resume`.

Loads via the star-import chain anchored at `main.py` — every
symbol re-exports into `main`'s namespace so route
decorators reach the shared `app` instance.
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
from main import *  # noqa: E402,F401,F403
# IDE contract: PyCharm/Pyright can't trace `from X import *`, so
# every name resolved through the wildcard above would be flagged as
# "Unresolved reference". The explicit imports below resolve at runtime
# too (Python's import system caches; second-import is a dict lookup),
# so they're safe + they silence the IDE in every scope (TYPE_CHECKING
# blocks DON'T propagate into nested function/closure scopes).
from main import (  # noqa: E402,F401 — explicit for IDE; runtime via the * above
    AdminUser,
    HTTPException,
    Request,
    Settings,
    Tunable,
    _cache,
    _coerce_int_local,
    _ops_mod,
    _request_client_id,
    app,
    db_conn,
    get_setting,
    set_setting,
    tuning,
)

# `_shape_host_api_row` / `_merge_one_host` / `_get_host_provider_state`
# are defined in main_pkg.apps_routes. At runtime they reach this
# file via main's star-import chain — we CAN'T do a real
# `from main_pkg.apps_routes import ...` here because hosts_routes is
# itself loaded BY apps_routes's top-level import block, which would
# create a circular import. So the explicit import lives behind
# TYPE_CHECKING (False at runtime) to silence the IDE without
# triggering the cycle.
from typing import TYPE_CHECKING  # noqa: E402

if TYPE_CHECKING:
    from main_pkg.apps_routes import (  # noqa: F401
        _get_host_provider_state,
        _merge_one_host,
        _shape_host_api_row,
        invalidate_host_provider_cache,
    )
from typing import Any, Optional


# Load .env BEFORE any os.getenv() calls (including those done at import time
# in auth.py). The file lives in the /app bind-mount and travels with the
# rest of the source via CI rsync — nothing in docker-compose.yml depends on
# env_file, which sidesteps Portainer's web-editor inability to resolve host
# paths. `override=False` keeps any values set in the compose `environment:`
# block authoritative (e.g. DB_PATH).


# Re-import parent's namespace so decorators below find `app`,
# helpers, Pydantic models, etc.


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
    # Late imports — these helpers live in main_pkg.apps_routes which
    # is loaded BY this module's transitive import chain (cycle), so
    # the module-top explicit import lives behind TYPE_CHECKING above.
    # Runtime LOAD_GLOBAL doesn't fall through to module __getattr__,
    # so the explicit late-import is the only path that lands the
    # symbols in this function's lookup chain. Bulk-import inside
    # the function body — by call time apps_routes is fully loaded.
    from main_pkg.apps_routes import (
        _get_host_provider_state,
        _host_provider_lock,
        _peek_cached_host_provider_state,
        _populate_detected_ports,
        _shape_host_api_row,
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


from main_pkg.hosts_ssh_routes import *  # noqa: E402,F401,F403


# noinspection DuplicatedCode
def __getattr__(name):
    """Module-level resolver for cross-module underscore-prefixed leaks.
    Delegates to the shared helper so the 33-line PEP 562 implementation
    lives in one place. See main_pkg._resolver for the full rationale.
    The 5-line delegator IS duplicated across 12 files — PEP 562 requires
    one __getattr__ per module; suppress the duplicated-code hint."""
    # noinspection PyProtectedMember
    from main_pkg._resolver import resolve
    return resolve(__name__, name)
