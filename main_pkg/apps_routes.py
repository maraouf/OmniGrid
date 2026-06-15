"""Apps feature (service catalog + instances) endpoints —
`/api/services/discover/{host_id}/apply`, `/api/services/catalog/*`,
`/api/apps`, `/api/apps/instances`. Backed by
`logic/service_catalog.py` + `logic/service_sampler.py`.

Loads via the star-import chain anchored at `main.py` — every
symbol re-exports into `main`'s namespace so route
decorators reach the shared `app` instance.
"""
"""Continuation of `main` — second chunk in the chain.

Loading order:
  1. main.py runs top half (defines `app`, helpers, models).
  2. main.py end: `from main_pkg.admin_ai_routes import *` triggers load.
  3. main_pkg.admin_ai_routes top: `from main import *` pulls main's
     top-half symbols. Body runs; more routes register.
  4. main_pkg.admin_ai_routes finishes; main.py continues with the next
     star-import (main_pkg.hosts_routes), which now sees every
     core-defined symbol via main's namespace.
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
# Runtime: `from main import *` brings every public + private symbol
# from main.py + already-loaded sibling chunks into our namespace,
# including stdlib re-exports (asyncio, json, os, sqlite3, time, Any,
# Iterable, Optional, ...) that main.py imports at its own top.
from main import *  # noqa: E402,F401,F403
import httpx  # noqa: F401,F811  (used at runtime; star-import shadow flags as unresolved)
from typing import Any, Optional
# asyncio / json / os / time are stdlib modules main.py imports at its top, so
# `from main import *` already provides them. They're pulled via the explicit
# `from main import (...)` block below (NOT a direct `import asyncio`) so the IDE
# binds the uses to that import — a direct stdlib import reads as "unused"
# because PyCharm resolves the uses to the wildcard-shadow instead.

# IDE contract: PyCharm/Pyright can't trace `from X import *`, so the
# wildcard above leaves every resolved name flagged as "Unresolved
# reference" — including names referenced inside nested function /
# closure scopes (TYPE_CHECKING-block imports DON'T propagate into
# those for PyCharm). The explicit imports below resolve at runtime
# too (main's body has already defined these symbols by the time this
# module is loaded from main's tail star-import chain), so they're a
# safe no-op runtime-wise + a full silencing of the IDE.
from main import (  # noqa: E402,F401  — re-imports for IDE static-analysis
    sqlite3,
    AdminUser,
    CurrentUser,
    HTTPException,
    Request,
    Response,
    Settings,
    Tunable,
    _actor_from,
    _cache,
    _coerce_int_local,
    _events,
    _gather,
    _gather_stats,
    _ops_mod,
    _request_client_id,
    active_host_stats_providers,
    app,
    asyncio,
    db_conn,
    get_setting,
    json,
    os,
    set_setting,
    time,
    tuning,
)
# `_clean_host_services` lives in main_pkg.hosts_routes — same chain-
# order problem as `_load_hosts_config` below: top-level import would
# trigger hosts_routes' tail chain and 404 every apps_routes decorator
# below this point. Resolves at runtime via the centralized wire-fixer
# at main.py's tail (`_wire_cross_module_underscore_globals`). The
# TYPE_CHECKING import below silences the IDE without triggering the
# cycle at runtime.
# `_load_hosts_config` is defined in main_pkg.hosts_routes. We CAN'T
# import it at the top level — hosts_routes loads AFTER apps_routes in
# main.py's route-registration chain (apps_routes is imported first, then
# hosts_routes, with auth_routes mounting the static catch-all LAST). A
# top-level import here would trigger hosts_routes' tail chain BEFORE
# apps_routes finishes registering its decorators, putting every
# apps_routes route AFTER the catch-all and 404'ing them.
# Deferred via TYPE_CHECKING (False at runtime → no chain trigger).
# Runtime resolution happens via `from main import *` (line 57) which
# re-exports the symbol once main's namespace has been populated.
from typing import TYPE_CHECKING as _TYPE_CHECKING  # noqa: E402

if _TYPE_CHECKING:
    from main_pkg.hosts_routes import (  # noqa: F401
        _load_hosts_config as _impl_load_hosts_config,
        _clean_host_services,
    )
    # Moved to main_pkg.hosts_merge_routes in the apps_routes split; still
    # called from the Apps section here. Resolved at runtime via the
    # wire-fixer (main._wire_cross_module_underscore_globals); this
    # TYPE_CHECKING import silences the IDE without triggering the chain.
    from main_pkg.hosts_merge_routes import _populate_detected_ports  # noqa: F401


def _load_hosts_config():
    """Lazy delegate to ``main_pkg.hosts_routes._load_hosts_config``.

    Top-level import would trigger hosts_routes' tail chain (auth_routes
    mounts the StaticFiles catch-all) BEFORE apps_routes finishes
    registering its decorators — every apps_routes route would land
    AFTER the catch-all and 404. Resolving inside the function lets
    apps_routes' module body finish first; by call time main is fully
    loaded so the import is a sys.modules cache hit (no chain trigger)."""
    from main_pkg.hosts_routes import _load_hosts_config as _impl
    return _impl()


def _persist_host_services(hosts: list, target_idx: int, services: list) -> None:
    """Validate ONE host's services[] via the canonical
    ``_clean_host_services`` validator, then persist the whole
    hosts_config.

    Single choke point for every apps-route that mutates a host's
    chips, so a future write path can't drift into a validator bypass
    (the operator-controlled name / url / icon overrides MUST be cleaned
    before they land on disk). Deliberately scoped to the mutated host —
    it does NOT re-clean the other hosts' services, since
    ``_clean_host_services`` drops un-whitelisted keys and re-cleaning an
    untouched host could strip a field written by a different code path.
    Kept local to apps_routes (rather than a cross-module
    ``hosts_routes.persist_hosts_config``) to avoid the star-import
    chain-order wiring; both callers live here.
    """
    hosts[target_idx]["services"] = _clean_host_services(services)
    set_setting(Settings.HOSTS_CONFIG, json.dumps(hosts))
    # Drop the cached `/api/apps` aggregate so a chip edit shows on the
    # next page load instead of waiting out the short TTL.
    # cache-drop is best-effort; a failure here must never block the settings
    # write that already committed above.
    # noinspection PyBroadException
    try:
        from logic import service_catalog as _sc
        _sc.invalidate_list_apps_cache()
    except Exception:  # noqa: BLE001
        pass


def _chip_app_is_fleet(chip: dict) -> bool:
    """True when the chip's app AGGREGATES across instances (declares
    ``FLEET_SKILLS``) — Pi-hole / AdGuard. Those apps render ONE aggregated
    card for every instance, so a single ``show_extras`` is shared across all
    of them. Every other app (qBittorrent, the *arr family, …) renders a card
    PER instance and keeps ``show_extras`` per-instance."""
    # noinspection PyBroadException
    try:
        from logic.apps import registry as _reg  # noqa: PLC0415
        # noinspection PyProtectedMember
        slug = _reg._chip_slug(chip)  # registry's canonical slug resolver (shared)
        mod = _reg.module_for_slug(slug) if slug else None
        return bool(getattr(mod, "FLEET_SKILLS", False))
    except Exception:  # noqa: BLE001
        return False


def _sync_show_extras_across_app(hosts: list, source_chip: dict, value: Any) -> None:
    """Mirror a per-instance ``show_extras`` override onto EVERY chip of the
    same app (same ``catalog_id``) across ALL hosts — but ONLY for AGGREGATE
    (fleet) apps (Pi-hole / AdGuard), whose card is ONE aggregated block for
    every instance, so a single "Show extras" control governs the whole app.
    Every NON-aggregate app (qBittorrent, the *arr family, …) renders a card
    PER instance, so its ``show_extras`` stays PER-INSTANCE and is NOT synced —
    editing one instance's extras must not touch the others.

    ``value`` is the bool to set on every sibling, or a non-bool (the SPA's
    inherit sentinel) to CLEAR the override. Mutates ``hosts`` in place; the
    caller persists the whole config via ``_persist_host_services``. Chips with
    no ``catalog_id`` (manual, not catalog-linked) are left alone.

    Only an EXPLICIT show/hide (``value`` is a bool) propagates. A
    clear-to-inherit (non-bool sentinel) stays per-instance so saving an
    untouched admin editor (form ``show_extras=null``) never clobbers a
    sibling's explicit setting; the gear-flip always sends a bool, so it
    always syncs."""
    if not isinstance(value, bool):
        return
    cid = _coerce_int_local(source_chip.get("catalog_id"))
    if cid is None:
        return
    # Non-aggregate apps keep show_extras per-instance — do NOT propagate.
    if not _chip_app_is_fleet(source_chip):
        return
    for h in hosts:
        if not isinstance(h, dict):
            continue
        for c in (h.get("services") or []):
            if not isinstance(c, dict) or _coerce_int_local(c.get("catalog_id")) != cid:
                continue
            c["show_extras"] = value


@app.post("/api/services/discover/{host_id}/apply")
async def api_services_discover_apply(host_id: str, payload: dict[str, Any], request: Request, _admin: AdminUser):
    """Admin-only: bulk-bind a set of catalog templates to a host.

    Body shape:
        {
            "catalog_ids":   [1, 5, 7],   # required; templates to pin
            "probe_enabled": true         # optional; default true
        }

    Iterates the requested catalog_ids and creates one chip per
    template by reusing the same pin logic. Returns a per-template
    result list so the SPA can show "3 of 4 pinned" toasts:

        {
            "host_id": str,
            "applied": [{catalog_id, name, service_idx}, ...],
            "skipped": [{catalog_id, reason}, ...]
        }

    Idempotent on already-bound catalog_ids (skipped with
    ``reason="already_bound"``). Validation + persistence go through the
    shared ``_persist_host_services`` choke point (which runs
    ``_clean_host_services`` before ``set_setting``), so the contract
    stays uniform with the Admin → Hosts editor save path.
    """
    raw_ids = payload.get("catalog_ids") or []
    if not isinstance(raw_ids, list):
        raise HTTPException(400, "catalog_ids must be a list")
    probe_enabled = bool(payload.get("probe_enabled", True))
    # Load host + existing chips ONCE; mutate locally + persist ONCE at the
    # end so we don't write the settings row N times for an N-pin apply.
    hosts = _load_hosts_config()
    target_idx = -1
    for i, row in enumerate(hosts):
        if (row.get("id") or "").strip() == host_id:
            target_idx = i
            break
    if target_idx < 0:
        raise HTTPException(404, f"host not found: {host_id}")
    from logic.service_catalog import get_catalog_by_id as _get_cat
    existing_services = hosts[target_idx].get("services") or []
    if not isinstance(existing_services, list):
        existing_services = []
    existing_catalog_ids = {_coerce_int_local(chip.get("catalog_id")) for chip in existing_services
                            if isinstance(chip, dict)}
    existing_catalog_ids.discard(None)
    # Resolve the host's last-scan detected (open) ports ONCE so a multi-port
    # template only contributes the ports the host ACTUALLY has open — pinning
    # from Discover shouldn't apply the FULL template port list when the host
    # exposes only a subset. Empty detected set (host never scanned) leaves the
    # full template list intact (nothing to filter against).
    _detected_ports: set[int] = set()
    try:
        _dmerge: dict[str, Any] = {}
        _populate_detected_ports(host_id, _dmerge)
        for _dp in (_dmerge.get("detected_ports") or []):
            _pn = _coerce_int_local(_dp.get("port")) if isinstance(_dp, dict) else None
            if _pn:
                _detected_ports.add(_pn)
    except Exception as e:  # noqa: BLE001
        print(f"[apps] discover-apply detected-port resolve failed for {host_id!r}: {e}")
        _detected_ports = set()
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for raw_cid in raw_ids:
        cid = _coerce_int_local(raw_cid)
        if not cid:
            skipped.append({"catalog_id": raw_cid, "reason": "invalid_id"})
            continue
        if cid in existing_catalog_ids:
            skipped.append({"catalog_id": cid, "reason": "already_bound"})
            continue
        tpl = _get_cat(cid)
        if not tpl:
            skipped.append({"catalog_id": cid, "reason": "not_found"})
            continue
        # CLONE-ON-PIN: snapshot name + icon + ports onto the chip so it's
        # fully independent of the template (editing either side later does
        # not affect the other). catalog_id stays for Apps-view grouping +
        # pin-dedup. Same semantics as the per-template pin route.
        new_chip: dict[str, Any] = {
            "catalog_id": cid,
            "name": tpl.get("name") or "",
            "icon": tpl.get("icon") or tpl.get("slug") or "",
        }
        default_ports = list(tpl.get("default_ports") or [])
        # Apply ONLY the template ports the host actually has open (matched
        # against the last scan). Keeps a multi-port template from stamping
        # ports the host doesn't expose. Falls back to the full list when the
        # host has no scan data OR none of the template ports were detected
        # (so the chip is never left port-less).
        if _detected_ports and default_ports:
            _matched = [p for p in default_ports
                        if isinstance(p, dict) and _coerce_int_local(p.get("port")) in _detected_ports]
            if _matched:
                default_ports = _matched
        probe: dict[str, Any] = {"enabled": probe_enabled, "type": "tcp"}
        if default_ports:
            probe["ports"] = default_ports
        new_chip["probe"] = probe
        new_idx = len(existing_services)
        existing_services.append(new_chip)
        existing_catalog_ids.add(cid)
        applied.append({
            "catalog_id": cid,
            "name": tpl.get("name") or "",
            "service_idx": new_idx,
        })
    # Validate + persist through the shared choke point so the
    # operator-controlled payload (custom name / url / icon overrides)
    # can't land a malformed chip shape in the DB. Same validator the
    # Admin → Hosts editor save path uses — keeps the on-disk contract
    # uniform across every code path that mutates hosts_config.
    _persist_host_services(hosts, target_idx, existing_services)
    with db_conn() as _c:
        _ops_mod.write_admin_audit(
            _c, "services_discover_apply",
            target_kind="host",
            target_name=host_id,
            target_id=host_id,
            actor=_actor_from(request),
            message=(f"Bulk-pinned {len(applied)} app(s) to {host_id}"
                     + (f" — skipped {len(skipped)}" if skipped else "")),
            events_dict={"applied": applied, "skipped": skipped},
        )
    # SSE: emit a single `apps:bulk_pinned` frame so other tabs refresh
    # their `appsInstances` immediately without waiting on the 30s poll.
    # Same single-frame shape as `host:bulk_action_applied` (per the
    # the project conventions "Bulk-action endpoints emit ONE SSE event" rule):
    # one wire frame carrying the full applied + skipped lists, the
    # SPA's handler iterates server-side and patches state in place.
    # `client_id` ensures the originating tab self-filters and doesn't
    # re-fetch what it just wrote.
    try:
        _events.publish(
            "apps:bulk_pinned",
            {
                "host_id": host_id,
                "applied": applied,
                "skipped": skipped,
            },
            client_id=_request_client_id(request),
        )
    except Exception as e:  # noqa: BLE001
        # Best-effort — a missing SSE frame leaves the cross-tab refresh
        # to the 30s poll fallback; never let it roll back the operator's
        # actual write.
        print(f"[apps] bulk_pinned SSE publish failed: {e}")
    return {
        "host_id": host_id,
        "applied": applied,
        "skipped": skipped,
    }


# noinspection DuplicatedCode
@app.post("/api/services/catalog/{cid}/pin")
async def api_services_catalog_pin(cid: int, payload: dict[str, Any], request: Request, _admin: AdminUser):
    """Admin-only: pin a catalog template to a host.

    Creates a new chip in the target host's ``services[]`` array
    pre-filled from the template's defaults (name / icon / ports +
    `catalog_id` linkage so future template edits propagate). Operator
    overrides via `name` / `url` / `icon` / `probe_enabled` /
    `probe_type` are validated + persisted through the shared
    `_persist_host_services` choke point (which runs
    `_clean_host_services` before `set_setting`).

    Body shape:
        {
            "host_id":       str,           # required — target curated host
            "name":          str | null,    # optional override; defaults to template name
            "url":           str | null,    # optional clickable link
            "icon":          str | null,    # optional override; defaults to template icon
            "probe_enabled": bool,          # default true
            "probe_type":    "tcp" | "http" # default tcp
        }

    Returns the new chip's `service_idx` so the SPA can highlight /
    scroll-to / launch a manual probe immediately.
    """
    from logic import service_catalog as _sc
    template = _sc.get_catalog_by_id(cid)
    if template is None:
        raise HTTPException(404, f"catalog template {cid} not found")
    host_id = (payload.get("host_id") or "").strip()
    if not host_id:
        raise HTTPException(400, "host_id is required")
    # Verify the host exists in the curated list.
    hosts = _load_hosts_config()
    target_idx = -1
    for i, row in enumerate(hosts):
        if (row.get("id") or "").strip() == host_id:
            target_idx = i
            break
    if target_idx < 0:
        raise HTTPException(404, f"host not found in hosts_config: {host_id}")
    # Build the chip dict from template defaults + operator overrides.
    override_name = (payload.get("name") or "").strip()
    override_url = (payload.get("url") or "").strip()
    override_icon = (payload.get("icon") or "").strip()
    probe_enabled = bool(payload.get("probe_enabled", True))
    probe_type_raw = (payload.get("probe_type") or "tcp").strip().lower()
    probe_type = probe_type_raw if probe_type_raw in ("tcp", "http") else "tcp"
    # CLONE-ON-PIN: snapshot the template's name + icon onto the chip (the
    # supplied override wins, else fall back to the template's value). The
    # chip gets its OWN independent copy of every field — editing the
    # template OR the instance later does NOT affect the other (no live
    # inheritance). The catalog_id linkage is kept ONLY for Apps-view
    # grouping + pin-dedup, not for resolving display fields.
    new_chip: dict[str, Any] = {
        "catalog_id": cid,
        "name": override_name or (template.get("name") or ""),
        "icon": override_icon or (template.get("icon") or template.get("slug") or ""),
    }
    if override_url:
        new_chip["url"] = override_url
    # Probe sub-dict — copy template's default_ports verbatim so the
    # chip starts in multi-port mode when the template carries multiple
    # entries. Operator can edit per-chip ports via Admin → Hosts.
    default_ports = list(template.get("default_ports") or [])
    probe: dict[str, Any] = {"enabled": probe_enabled, "type": probe_type}
    if default_ports:
        probe["ports"] = default_ports
    new_chip["probe"] = probe
    # Append to the target host's services[] (preserve every other
    # chip already pinned).
    existing_services = hosts[target_idx].get("services") or []
    if not isinstance(existing_services, list):
        existing_services = []
    # Reject a duplicate pin — the same catalog app can't be pinned to the
    # same host twice (it would render as two identical chips). 409 so the
    # SPA surfaces "already pinned" instead of silently creating a dupe.
    # (The _clean_host_services dedup is the backend safety net; this is
    # the up-front, clearly-messaged guard.)
    for _ex in existing_services:
        if not isinstance(_ex, dict):
            continue
        _excid = _ex.get("catalog_id")
        # isinstance (not `is not None`) so the type checker narrows
        # dict.get()'s Any|None → int|str for the int() coercion.
        try:
            _is_dup = isinstance(_excid, (int, str)) and int(_excid) == cid
        except (TypeError, ValueError):
            _is_dup = False
        if _is_dup:
            raise HTTPException(
                409,
                f"'{template.get('name')}' is already pinned to {host_id}",
            )
    new_idx = len(existing_services)
    existing_services.append(new_chip)
    # Validate + persist through the shared choke point — same validator
    # the Admin → Hosts editor save path uses, so an operator-controlled
    # override payload (custom name / url / icon) can't land a malformed
    # chip shape in the DB.
    _persist_host_services(hosts, target_idx, existing_services)
    with db_conn() as _c:
        _ops_mod.write_admin_audit(
            _c, "services_pin",
            target_kind="host",
            target_name=host_id,
            target_id=host_id,
            actor=_actor_from(request),
            message=(f"Pinned app '{template.get('name')}' (catalog_id={cid}) "
                     f"to {host_id} as service_idx={new_idx}"),
        )
    return {
        "ok": True,
        "host_id": host_id,
        "service_idx": new_idx,
        "chip": new_chip,
        "catalog": template,
    }


def _find_host_idx(hosts: list, host_id: str) -> int:
    """Index of the curated host row with this id, or -1."""
    for i, row in enumerate(hosts):
        if isinstance(row, dict) and (row.get("id") or "").strip() == host_id:
            return i
    return -1


# noinspection DuplicatedCode
@app.patch("/api/services/{host_id}/{service_idx}")
async def api_service_edit(host_id: str, service_idx: int, payload: dict[str, Any],
                           request: Request, _admin: AdminUser):
    """Admin-only: edit one pinned chip's operator-facing fields.

    Accepts any subset of ``name`` / ``url`` / ``icon`` / ``probe_enabled``
    / ``probe_type``; only the keys present in the payload are applied.
    For ``name`` / ``icon`` an EMPTY string CLEARS the override so the
    chip re-inherits from its catalog template; for ``url`` it removes
    the link. Persists through the shared validated choke point and
    writes a ``services_edit`` audit row."""
    hosts = _load_hosts_config()
    target_idx = _find_host_idx(hosts, host_id)
    if target_idx < 0:
        raise HTTPException(404, f"host not found: {host_id}")
    chips = hosts[target_idx].get("services") or []
    if not isinstance(chips, list) or service_idx < 0 or service_idx >= len(chips):
        raise HTTPException(404, f"service_idx {service_idx} out of range for host {host_id}")
    chip = chips[service_idx]
    if not isinstance(chip, dict):
        raise HTTPException(400, "service entry malformed")
    if "name" in payload:
        v = (payload.get("name") or "").strip()
        chip["name"] = v[:64] if v else ""
        if not v:
            chip.pop("name", None)  # blank → re-inherit from template
    if "url" in payload:
        v = (payload.get("url") or "").strip()
        if v:
            chip["url"] = v[:256]
        else:
            chip.pop("url", None)
    if "icon" in payload:
        v = (payload.get("icon") or "").strip()
        if v:
            chip["icon"] = v[:64]
        else:
            chip.pop("icon", None)  # blank → re-inherit from template
    # Docker linkage — empty string CLEARS the link (no inline actions);
    # a non-empty value links the chip to a Portainer container / stack.
    if "docker_stack" in payload:
        v = (payload.get("docker_stack") or "").strip()
        if v:
            chip["docker_stack"] = v[:128]
        else:
            chip.pop("docker_stack", None)
    if "docker_container" in payload:
        v = (payload.get("docker_container") or "").strip()
        if v:
            chip["docker_container"] = v[:256]
        else:
            chip.pop("docker_container", None)
    # Swarm node / host of the linked container (disambiguates a name
    # shared across hosts). Empty clears it; cleared automatically when
    # the link is switched to a service.
    if "docker_host" in payload:
        v = (payload.get("docker_host") or "").strip()
        if v:
            chip["docker_host"] = v[:256]
        else:
            chip.pop("docker_host", None)
    # Per-instance show_extras override — tri-state (null /
    # absent = inherit from the catalog template's default,
    # true / false = explicit operator override). Only `bool`
    # values stamp the field; anything else (including the
    # SPA's `null` sentinel for "inherit") clears the override
    # so a future template default-flip propagates without
    # re-saving the chip.
    if "show_extras" in payload:
        v = payload.get("show_extras")
        if isinstance(v, bool):
            chip["show_extras"] = v
        else:
            chip.pop("show_extras", None)
        # The gear-flip card "Show extras" toggle + this admin instance
        # editor are ONE control (operator request) — keep show_extras
        # UNIFORM across every instance of this app so editing either
        # surface updates the other and the aggregated card never disagrees
        # with the admin setting. Mirror the value onto every sibling chip
        # (same catalog_id, all hosts); _persist_host_services below dumps
        # the whole config so the cross-host mutations land.
        _sync_show_extras_across_app(hosts, chip, v)
    # Per-instance api_key — keep-current-if-blank contract
    # shared with every other secret in OmniGrid. Non-empty
    # string overwrites; empty / whitespace / missing preserves
    # the stored value. The field is never returned in the
    # clear (see `_shape_apps_instances` which stamps
    # `api_key_set` only). Stored under `services[].api_key`
    # as a bounded string (max 512 chars).
    if "api_key" in payload:
        v = (payload.get("api_key") or "").strip()
        if v:
            chip["api_key"] = v[:512]
        # blank → keep current (no chip.pop — the existing
        # value carries forward).
    # Per-instance 2FA TOTP secret — SECRET, keep-current-if-blank (same
    # contract as api_key). Non-empty overwrites; blank preserves the stored
    # value. Never returned in the clear (only a `totp_secret_set` flag).
    if "totp_secret" in payload:
        v = (payload.get("totp_secret") or "").strip()
        if v:
            chip["totp_secret"] = v[:128]
    # Per-instance username — the NON-secret half of a Basic-auth pair
    # (e.g. AdGuard Home). Returned in the clear, so the SPA round-trips
    # it: a non-empty value overwrites, an explicit blank CLEARS it (no
    # keep-current — that contract is for secrets only). Only the apps
    # whose editor declares a username input ever send a non-empty value.
    if "username" in payload:
        v = (payload.get("username") or "").strip()
        if v:
            chip["username"] = v[:128]
        else:
            chip.pop("username", None)
    # Seerr TMDB config. `tmdb_api_key` is a SECRET → keep-current-if-blank
    # (non-empty overwrites; blank preserves the stored value; never
    # returned in the clear). The two base URLs are NON-secret → a blank
    # CLEARS them (so the app falls back to its public TMDB defaults).
    if "tmdb_api_key" in payload:
        v = (payload.get("tmdb_api_key") or "").strip()
        if v:
            chip["tmdb_api_key"] = v[:512]
        # blank → keep current (no pop).
    for _tf in ("tmdb_base_url", "tmdb_image_base_url"):
        if _tf in payload:
            v = (payload.get(_tf) or "").strip()
            if v:
                chip[_tf] = v[:256]
            else:
                chip.pop(_tf, None)
    # Per-instance averages window (Speedtest "Avg of last N tests").
    # Bounded 2..60; a blank / out-of-range / non-int value CLEARS the
    # override so the app falls back to its default (10). Returned in the
    # clear so the Admin editor AND the gear-flip card settings round-trip
    # it. Both surfaces send it through THIS handler.
    if "avg_window" in payload:
        _aw_raw = payload.get("avg_window")
        _awi = None
        if isinstance(_aw_raw, (int, str)) and str(_aw_raw).strip() != "":
            try:
                # CLAMP to 2..60 (don't drop): 90 -> 60, honouring intent
                # instead of reverting to the default. Blank / unparseable
                # clears the override (-> app default).
                _awi = max(2, min(60, int(_aw_raw)))
            except (TypeError, ValueError):
                _awi = None
        if _awi is not None:
            chip["avg_window"] = _awi
        else:
            chip.pop("avg_window", None)
    # Per-instance Speedtest below-floor reliability floor (Mbps) — the
    # operator's own ISP-advertised download floor; the card flags the % of
    # successful tests below it. 0 / blank / unparseable CLEARS it (-> OFF).
    # Clamp 0..100000. Returned in the clear so the Admin editor AND the
    # gear-flip card settings round-trip it (both call THIS handler).
    if "speed_floor_mbps" in payload:
        _sf_raw = payload.get("speed_floor_mbps")
        _sfv = None
        if isinstance(_sf_raw, (int, float, str)) and str(_sf_raw).strip() != "":
            try:
                _sfv = max(0.0, min(100000.0, float(_sf_raw)))
            except (TypeError, ValueError):
                _sfv = None
        if _sfv and _sfv > 0:
            chip["speed_floor_mbps"] = _sfv
        else:
            chip.pop("speed_floor_mbps", None)
    # Per-instance data-cache TTL (seconds) — operator override of the app
    # module's default. Clamp to 5..3600; blank / unparseable clears the
    # override so the app default applies. Returned in the clear (round-trips
    # to the editor) — NOT a secret.
    if "cache_ttl" in payload:
        _ct_raw = payload.get("cache_ttl")
        _cti = None
        if isinstance(_ct_raw, (int, str)) and str(_ct_raw).strip() != "":
            try:
                _cti = max(5, min(3600, int(_ct_raw)))
            except (TypeError, ValueError):
                _cti = None
        if _cti is not None:
            chip["cache_ttl"] = _cti
        else:
            chip.pop("cache_ttl", None)
    # Per-instance TLS-verification toggle — for apps that talk HTTPS to a
    # self-signed / internal cert (e.g. NPM admin UI). Present overwrites;
    # absent leaves the stored value (so apps without the toggle keep their
    # module default). Returned in the clear (round-trips to the editor).
    if "verify_tls" in payload:
        chip["verify_tls"] = bool(payload.get("verify_tls"))
    # Per-instance Seerr "suggest a movie" pool sizing. Clamp each; a blank /
    # out-of-range / non-int value CLEARS the override so the module default
    # applies (8 attempts / page-200 catalogue depth). Returned in the clear
    # (round-trips to the editor) — NOT secrets.
    for _sf, _slo, _shi in (("suggest_page_attempts", 1, 50),
                            ("suggest_max_page", 10, 500)):
        if _sf in payload:
            _sv_raw = payload.get(_sf)
            _svi = None
            if isinstance(_sv_raw, (int, str)) and str(_sv_raw).strip() != "":
                try:
                    _svi = max(_slo, min(_shi, int(_sv_raw)))
                except (TypeError, ValueError):
                    _svi = None
            if _svi is not None:
                chip[_sf] = _svi
            else:
                chip.pop(_sf, None)
    _probe_raw = chip.get("probe")
    probe = _probe_raw if isinstance(_probe_raw, dict) else {}
    if "probe_enabled" in payload:
        probe["enabled"] = bool(payload.get("probe_enabled"))
    if "probe_type" in payload:
        pt = (payload.get("probe_type") or "tcp").strip().lower()
        probe["type"] = pt if pt in ("tcp", "http") else "tcp"
    if "ports" in payload and isinstance(payload.get("ports"), list):
        # Raw per-port list straight from the editor — _clean_host_services
        # (run by _persist_host_services) validates each entry's port /
        # protocol / label / probe_path / probe_status, so malformed rows
        # are dropped here without a second validator.
        probe["ports"] = payload["ports"]
    chip["probe"] = probe
    chips[service_idx] = chip
    _persist_host_services(hosts, target_idx, chips)
    with db_conn() as _c:
        _ops_mod.write_admin_audit(
            _c, "services_edit",
            target_kind="host", target_name=host_id, target_id=host_id,
            actor=_actor_from(request),
            message=f"Edited app chip service_idx={service_idx} on {host_id}",
        )
    # Return the post-validation chip so the SPA can patch its row.
    fresh = _load_hosts_config()
    fi = _find_host_idx(fresh, host_id)
    out_chip: dict = {}
    if fi >= 0:
        svcs = fresh[fi].get("services") or []
        if 0 <= service_idx < len(svcs) and isinstance(svcs[service_idx], dict):
            out_chip = svcs[service_idx]
    return {"ok": True, "host_id": host_id, "service_idx": service_idx, "chip": out_chip}


# --------------------------------------------------------------------------
# Per-app dispatcher endpoints — slug-keyed, fully generic.
#
# Per-app modules live under `logic/apps/<slug>.py`. Each module owns
# the upstream-API specifics (base URL resolution, credential probe,
# data fetch + cache). The two endpoints below resolve the chip's
# catalog template → slug, dispatch via `logic.apps.registry`, and
# return the module's response unchanged. Adding a new app does NOT
# touch this file — drop a module under `logic/apps/` + register in
# `logic/apps/registry.py`.
# --------------------------------------------------------------------------
def _resolve_chip_app_module(host_id: str, service_idx: int):
    """Common prelude — load chip + look up its per-app module.

    Returns ``(host_row, chip, module)`` or raises HTTPException(404)
    / HTTPException(400) on the usual not-found / no-app-registered
    failure modes. The slug is derived from the chip's catalog_id;
    operator-edited chips that DROPPED the catalog link fall through
    to "no module" so the generic edit path stays the only surface."""
    hosts = _load_hosts_config()
    target_idx = _find_host_idx(hosts, host_id)
    if target_idx < 0:
        raise HTTPException(404, f"host not found: {host_id}")
    chips = hosts[target_idx].get("services") or []
    if not isinstance(chips, list) or service_idx < 0 or service_idx >= len(chips):
        raise HTTPException(404,
                            f"service_idx {service_idx} out of range for host {host_id}")
    chip = chips[service_idx]
    if not isinstance(chip, dict):
        raise HTTPException(400, "service entry malformed")
    from logic.apps import registry as apps_registry
    from logic.service_catalog import list_catalog, coerce_int as _ci
    cid = _ci(chip.get("catalog_id"))
    slug = ""
    if cid is not None:
        for tpl in list_catalog():
            try:
                if int(tpl.get("id") or 0) == int(cid):
                    slug = (tpl.get("slug") or "").strip()
                    break
            except (TypeError, ValueError):
                continue
    mod = apps_registry.module_for_slug(slug) if slug else None
    return hosts[target_idx], chip, mod


@app.get("/api/services/{host_id}/{service_idx}/app-suggest/{kind}")
async def api_service_app_suggest(host_id: str, service_idx: int, kind: str,
                                  request: Request, _admin: AdminUser):
    """Admin-only: generic per-app SUGGESTION dispatcher (read-only, no state
    change). The chip's catalog slug selects the per-app module; if it defines
    ``suggest(kind, host_row, chip, *, host_id, service_idx, params)`` the call
    is forwarded with the query params (e.g. ``?days=30``) and its dict returned.
    Used by the Speedtest editor's "Recommend floor" button (``kind=speed-floor``
    → a floor derived from the chip's own speed-test history). Apps without a
    suggest hook return 400."""
    host_row, chip, mod = _resolve_chip_app_module(host_id, service_idx)
    if mod is None or not hasattr(mod, "suggest"):
        raise HTTPException(400, "no suggestions for this app")
    try:
        result = await mod.suggest(kind, host_row, chip, host_id=host_id,
                                   service_idx=service_idx,
                                   params=dict(request.query_params))
    except ValueError as e:
        raise HTTPException(404, str(e))
    except (RuntimeError,) as e:  # noqa: BLE001
        result = {"ok": False, "detail": str(e)}
    return result


@app.post("/api/services/{host_id}/{service_idx}/test-credential")
async def api_service_test_credential(host_id: str, service_idx: int,
                                      payload: dict[str, Any],
                                      request: Request, _admin: AdminUser):
    """Admin-only: probe the chip's app credentials.

    Generic test-before-Save dispatcher. The chip's catalog slug
    selects the per-app module from `logic/apps/registry`; the
    module's ``test_credential(host_row, chip, candidate_key)``
    coroutine performs the actual upstream probe + returns the
    SPA-shaped result. Apps without a registered module return
    400 ("no test path for this app"). Audited via the standard
    ``services_test`` op_type so the operator can trace probe
    attempts in History."""
    host_row, chip, mod = _resolve_chip_app_module(host_id, service_idx)
    if mod is None or not hasattr(mod, "test_credential"):
        raise HTTPException(400, "no test path for this app")
    candidate_key = (payload.get("api_key") or "").strip()
    # Overlay the LIVE editor URL (when the operator typed / changed the
    # instance URL but hasn't saved yet) onto a chip copy so the per-app
    # module's resolve_base_url sees the URL being tested, not the stale
    # saved one. Test-before-save needs the current field value — without
    # this, testing a brand-new (unsaved) chip reports "no upstream URL
    # configured" even after the operator filled the URL in. Generic — every
    # app's resolve_base_url reads chip['url'] first.
    _live_url = (payload.get("url") or "").strip()
    if _live_url:
        chip = {**chip, "url": _live_url}
    try:
        # Forward the full payload so apps with multi-field credentials
        # (e.g. AdGuard's username + password) can validate them together
        # pre-save. Single-secret apps ignore it (they read candidate_key).
        result = await mod.test_credential(host_row, chip, candidate_key, payload=payload)
    except (RuntimeError, ValueError) as e:  # noqa: BLE001
        result = {"ok": False, "detail": str(e), "status": 0}
    with db_conn() as _c:
        _ops_mod.write_admin_audit(
            _c, "services_test",
            target_kind="host", target_name=host_id, target_id=host_id,
            actor=_actor_from(request),
            message=(f"Tested credentials for service_idx={service_idx} on {host_id} "
                     f"(ok={result.get('ok')})"),
        )
    # On a PASSING test, stamp the chip's last-successful-test timestamp so the
    # editor's "✓ Last tested Xm ago" chip survives a reload (surfaced via
    # iter_instances). Best-effort: a persist failure must never break the test
    # response the SPA is waiting on.
    if isinstance(result, dict) and result.get("ok"):
        # noinspection PyBroadException
        try:
            import time as _time
            hosts = _load_hosts_config()
            tidx = _find_host_idx(hosts, host_id)
            if tidx >= 0:
                svcs = hosts[tidx].get("services") or []
                if (isinstance(svcs, list) and 0 <= service_idx < len(svcs)
                    and isinstance(svcs[service_idx], dict)):
                    svcs[service_idx]["last_test_ok_ts"] = int(_time.time())
                    _persist_host_services(hosts, tidx, svcs)
        except Exception:  # noqa: BLE001
            pass
    return result


@app.post("/api/apps/plex/auth/start")
async def api_apps_plex_auth_start(_admin: AdminUser):
    """Admin-only: begin the Plex "Sign in to Plex" OAuth PIN flow (the seamless
    device flow Tautulli / Overseerr use). Returns the auth-page URL the SPA
    opens in a popup + the pin id / code it polls — so the operator never has to
    paste an X-Plex-Token by hand."""
    from logic.apps import plex as _plex  # noqa: PLC0415
    return await _plex.start_auth()


@app.get("/api/apps/plex/auth/poll")
async def api_apps_plex_auth_poll(_admin: AdminUser, pin_id: int = 0, code: str = ""):
    """Admin-only: poll a pending Plex OAuth PIN. Returns ``{ok, token}`` once
    the operator has authorised in the popup, ``{ok, pending}`` while waiting."""
    from logic.apps import plex as _plex  # noqa: PLC0415
    return await _plex.poll_auth(pin_id, code)


@app.get("/api/services/{host_id}/{service_idx}/app-data")
async def api_service_app_data(host_id: str, service_idx: int,
                               _admin: AdminUser,
                               force: bool = False):
    """Admin-only: fetch the per-app expanded-card data for one chip.

    Generic dispatcher. The chip's catalog slug selects the per-app
    module; the module's ``fetch_data(host_row, chip, *, host_id,
    service_idx, force)`` coroutine returns the SPA-shaped data
    dict (typically ``{latest, averages, series, fetched_at}`` for
    Speedtest-style apps; future apps may shape it differently —
    the SPA's app-specific template is the contract).

    Apps without a registered module return 400. The module is
    free to cache per (host_id, service_idx); ``?force=true`` is
    forwarded so the operator can force a fresh upstream fetch.
    """
    host_row, chip, mod = _resolve_chip_app_module(host_id, service_idx)
    if mod is None or not hasattr(mod, "fetch_data"):
        raise HTTPException(400, "no data path for this app")
    slug = getattr(mod, "__name__", "?").rsplit(".", 1)[-1]
    # Wall-clock budget UNDER the reverse-proxy proxy_read_timeout. Without
    # it, a slow upstream (e.g. Tdarr mid-bloated-scan) lets the request hang
    # until the FRONT proxy emits its own gateway 504 — which the SPA shows as
    # "<app> fetch failed: HTTP 504" but leaves NO server-side trace of which
    # app/host timed out. By failing first, OmniGrid logs the timeout + returns
    # an identifiable 504 (same pattern as /api/hosts/one/{id}'s 30s budget).
    _budget = float(tuning.tuning_int(Tunable.APPS_ROUTE_BUDGET_SECONDS))
    try:
        return await asyncio.wait_for(
            mod.fetch_data(host_row, chip,
                           host_id=host_id, service_idx=service_idx,
                           force=force),
            timeout=_budget)
    except asyncio.TimeoutError:
        print(f"[apps] error: app-data fetch TIMED OUT host={host_id} "
              f"svc_idx={service_idx} app={slug} budget={_budget:.0f}s "
              f"(upstream too slow — raised our own 504 before the reverse "
              f"proxy could emit an unlogged gateway timeout)")
        raise HTTPException(
            504, f"{slug} app-data fetch exceeded {_budget:.0f}s budget")
    except ValueError as e:  # caller-side errors (missing key / URL)
        # Generic per-app-data failure log so EVERY app (not just the
        # ones with their own module-level logging) is traceable in
        # stdout / Admin -> Logs with the host + chip + module that
        # failed. ValueError = operator-fixable config (missing key /
        # URL) -> WARN (use the `warning:` marker the severity
        # classifier in logic/logs.py keys on).
        print(f"[apps] warning: app-data config issue host={host_id} "
              f"svc_idx={service_idx} app={slug}: {e}")
        raise HTTPException(400, str(e))
    except RuntimeError as e:  # upstream errors
        # Upstream actually failed (404 / auth / timeout) -> ERROR (the
        # `error:` marker routes it to the ERROR bucket).
        print(f"[apps] error: app-data fetch failed host={host_id} "
              f"svc_idx={service_idx} app={slug}: {e}")
        raise HTTPException(502, str(e))


# TMDB/CDN poster hosts the SPA loads DIRECT (no api_key needed); everything
# else a per-app skill emits as a thumbnail (Plex art behind Tautulli, Bazarr
# posters) needs the app's own credential to fetch — and that credential MUST
# stay server-side. This route is the per-app analogue of the TMDB
# `/api/image-proxy`: the OmniGrid SERVER fetches the upstream art using the
# chip's stored key (built by the module's `image_proxy_url` hook), then
# streams the bytes back so the api_key never reaches the browser DOM.
_APP_IMAGE_PROXY_MAX_BYTES = 10 * 1024 * 1024


def _sniff_image_type(data: bytes) -> str:
    """Best-effort image content-type from magic bytes (JPEG / PNG / GIF / WebP
    / BMP / ICO). Returns ``""`` when the bytes aren't a recognised image.

    Defence for upstreams (or a reverse proxy) that serve a valid image with a
    wrong / generic content-type (``application/octet-stream``, ``text/plain``)
    — without it the proxy's ``image/*`` check would 415 a perfectly good
    cover."""
    if not data or len(data) < 12:
        return ""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    if data[:4] == b"\x00\x00\x01\x00":
        return "image/x-icon"
    return ""


@app.get("/api/services/{host_id}/{service_idx}/image-proxy")
async def api_service_image_proxy(host_id: str, service_idx: int,
                                  _admin: AdminUser, path: str = ""):
    """Admin-only: proxy ONE authenticated thumbnail for a chip's app.

    ``path`` is an OPAQUE, app-relative image reference that the chip's own
    skill emitted in its rich-items result (e.g. a Plex metadata thumb key for
    Tautulli, a ``/...`` poster path for Bazarr). The per-app module's
    ``image_proxy_url(chip, base, path) -> (url, headers)`` hook validates the
    path (rejecting absolute URLs / traversal — SSRF guard) and builds the
    absolute upstream URL + auth headers against the chip's OWN base, so the
    fetch can only ever hit the configured app host. We stream the bytes back
    with the upstream content-type so the api_key never lands in the browser.

    Apps whose thumbnails are public CDN URLs (the *arr family via TMDB
    ``remoteUrl``, Seerr avatars via plex.tv) do NOT route here — the SPA loads
    those direct. Apps without an ``image_proxy_url`` hook return 400."""
    host_row, chip, mod = _resolve_chip_app_module(host_id, service_idx)
    if mod is None or not hasattr(mod, "image_proxy_url"):
        raise HTTPException(400, "no image proxy for this app")
    # The module resolves its own base URL internally (encapsulation — the
    # route stays free of the apps package's internals) and raises ValueError
    # on a bad / missing path or unconfigured URL.
    try:
        url, headers = mod.image_proxy_url(host_row, chip, path or "")
    except (ValueError, TypeError) as e:
        raise HTTPException(400, f"bad image path: {e}")
    from urllib.parse import urljoin, urlsplit  # noqa: PLC0415
    try:
        parts = urlsplit((url or "").strip())
    except (ValueError, TypeError):
        raise HTTPException(400, "module produced a bad url")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise HTTPException(400, "module produced a non-http url")
    # Disk-cache hit — serve without re-fetching. Keyed by the resolved upstream
    # URL + a NON-SENSITIVE per-chip cache tag (never the raw credential — see
    # logic/image_cache.py:_key). A public-CDN cover (no auth header) gets an
    # empty tag so it dedups across providers; an authenticated per-chip image
    # gets a coarse "<host>:<idx>" tag so it stays distinct.
    from logic import image_cache  # noqa: PLC0415
    _authed = bool(headers and (headers.get("Authorization") or headers.get("X-Api-Key")))
    _cache_tag = f"{host_id}:{service_idx}" if _authed else ""
    # Disk get/put run off the event loop (file I/O + opportunistic prune scan).
    _hit = await asyncio.to_thread(image_cache.get, url, _cache_tag)
    if _hit is not None:
        return Response(content=_hit[0], media_type=_hit[1],
                        headers={"Cache-Control": "public, max-age=604800, immutable",
                                 "X-OmniGrid-Cache": "hit"})
    # Manual redirect loop (follow_redirects=False) so EACH hop's host is
    # re-validated before following — the module hook only validated hop 1, so a
    # 30x off an allowlisted host is otherwise an SSRF escape (a redirect to a LAN
    # host / 169.254.169.254 cloud-metadata would be followed + cached). A
    # SAME-host redirect (http->https / trailing-slash normalisation on the
    # operator's own configured app) is always followed; a CROSS-host redirect is
    # followed ONLY when the module's optional image_redirect_allowed() hook
    # permits the target (e.g. _servarr's coverartarchive -> ia*.archive.org
    # cover-art hop) — and the auth headers are dropped on a cross-host hop so the
    # chip credential never leaks to a redirect target. Capped at 3 redirects.
    _redirect_ok = getattr(mod, "image_redirect_allowed", None)
    cur_url = url
    cur_headers = dict(headers or {})
    r = None
    try:
        # follow_redirects=False is httpx's default, but kept EXPLICIT — this
        # proxy walks redirects by hand (the per-hop SSRF re-validation below),
        # so auto-following would defeat the guard. Pinned against a future
        # httpx default change.
        # noinspection PyArgumentEqualDefault
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=False) as cli:
            for _hop in range(4):  # initial GET + up to 3 redirects
                r = await cli.get(cur_url, headers=cur_headers)
                if r.status_code not in (301, 302, 303, 307, 308):
                    break
                loc = (r.headers.get("location") or "").strip()
                if not loc:
                    break
                nxt = urljoin(cur_url, loc)
                np = urlsplit(nxt)
                if np.scheme not in ("http", "https") or not np.netloc:
                    raise HTTPException(502, "upstream redirect to a non-http url")
                same_host = (np.hostname or "").lower() == (urlsplit(cur_url).hostname or "").lower()
                if not same_host:
                    if not (_redirect_ok and _redirect_ok(host_row, chip, nxt)):
                        raise HTTPException(502, "upstream redirect to a disallowed host")
                    cur_headers = {"Accept": "*/*"}  # don't leak the chip credential cross-host
                cur_url = nxt
            else:
                raise HTTPException(502, "too many upstream redirects")
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        raise HTTPException(502, f"upstream image fetch failed: {type(e).__name__}")
    if r is None:
        raise HTTPException(502, "upstream image fetch failed")
    if r.status_code == 404:
        raise HTTPException(404, "image not found upstream")
    if r.status_code != 200:
        raise HTTPException(502, f"upstream returned HTTP {r.status_code}")
    body = r.content
    if len(body) > _APP_IMAGE_PROXY_MAX_BYTES:
        raise HTTPException(413, "upstream image too large")
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not ctype.startswith("image/"):
        # The content-type isn't image/* — but some upstreams mislabel a valid
        # image (octet-stream / text/plain), so sniff the magic bytes before
        # rejecting. A genuine image serves with the sniffed type; anything else
        # (e.g. an HTML login / SPA shell returned 200 for a bad-auth image
        # fetch) is a real 415.
        sniffed = _sniff_image_type(body)
        if not sniffed:
            # Diagnostic — a non-image 200 almost always means the upstream
            # served an HTML auth / login / SPA shell because the image fetch
            # wasn't authenticated (e.g. an *arr MediaCover route that wants the
            # apikey in the QUERY, not just the header). Log the content-type +
            # the first bytes (escaped) + the host so the 415 is actionable.
            try:
                snippet = body[:80].decode(errors="replace").replace("\n", " ").strip()
            except (ValueError, TypeError):
                snippet = repr(body[:80])
            print(f"[image-proxy] warning: non-image 200 from host={parts.netloc} "
                  f"path={parts.path} content-type={ctype or '(none)'} "
                  f"bytes={len(body)} first80={snippet!r}")
            # Surface the diagnostic in the response body too (visible in the
            # browser Network tab) so the operator can see WHAT the upstream
            # returned without digging through Admin → Logs.
            raise HTTPException(
                415,
                f"upstream returned non-image (content-type={ctype or 'none'}, "
                f"{len(body)} bytes, starts: {snippet[:60]!r}) — if this is HTML "
                f"the upstream isn't authenticating the image fetch")
        ctype = sniffed
    await asyncio.to_thread(image_cache.put, url, body, ctype, _cache_tag)
    return Response(content=body, media_type=ctype,
                    headers={"Cache-Control": "public, max-age=604800, immutable",
                             "X-OmniGrid-Cache": "miss"})


@app.post("/api/services/{host_id}/{service_idx}/skill/{skill_id}")
async def api_service_run_skill(host_id: str, service_idx: int, skill_id: str,
                                request: Request, _admin: AdminUser):
    """Admin-only: run one per-app SKILL on a chip (e.g. Speedtest's
    ``run_speedtest``).

    Generic dispatcher — the chip's catalog slug selects the per-app module;
    the module's ``run_skill(skill_id, host_row, chip, *, host_id,
    service_idx)`` coroutine performs the action + returns ``{ok, detail,
    status?}``. Gated on (a) the module DECLARING the skill in its ``SKILLS``
    tuple (404 otherwise) and (b) the app's api_key being set when the module
    ``requires_api_key()`` (400 otherwise). Both the app-drawer button AND the
    AI / Telegram-AI skill action route through here. Audited via the
    ``services_skill`` op_type so the operator can trace every skill run in
    History."""
    # Visibility: log the request up front + every gate decision so a
    # "the skill didn't run" report is never silent in stdout / Admin → Logs.
    print(f"[app_skill] INFO web skill request host={host_id} svc_idx={service_idx} "
          f"skill={skill_id} actor={_actor_from(request)}")
    host_row, chip, mod = _resolve_chip_app_module(host_id, service_idx)
    if mod is None or not hasattr(mod, "run_skill"):
        print(f"[app_skill] warning: web skill skipped — no skills for app at "
              f"host={host_id} svc_idx={service_idx} (skill={skill_id})")
        raise HTTPException(400, "no skills for this app")
    skills = getattr(mod, "SKILLS", ())
    skill = next((s for s in skills if isinstance(s, dict) and s.get("id") == skill_id), None)
    if skill is None:
        print(f"[app_skill] warning: web skill skipped — unknown skill {skill_id!r} "
              f"at host={host_id} svc_idx={service_idx}")
        raise HTTPException(404, f"unknown skill: {skill_id}")
    _req_key = getattr(mod, "requires_api_key", None)
    if callable(_req_key) and _req_key() and not (chip.get("api_key") or "").strip():
        print(f"[app_skill] warning: web skill skipped — api_key not set for "
              f"skill={skill_id} at host={host_id} svc_idx={service_idx}")
        raise HTTPException(400, "api_key not set for this app")
    # Optional free-form argument (e.g. Seerr's request-a-movie title).
    # Skills that don't declare `arg` simply ignore it. Body is optional —
    # the drawer button POSTs with no body; the AI dispatch sends {arg}.
    # `confirm` is the destructive-skill gate (below): the SPA sends it only
    # AFTER an operator confirm (inline chip / autonomous opt-in / Cmd-K
    # SweetAlert / fleet-card SweetAlert).
    skill_arg = ""
    skill_confirm = False
    try:
        _body = await request.json()
        if isinstance(_body, dict):
            skill_arg = str(_body.get("arg") or "").strip()[:512]
            skill_confirm = bool(_body.get("confirm"))
    except (ValueError, TypeError, UnicodeDecodeError):
        skill_arg = ""
    # Defence-in-depth: a destructive skill (e.g. an *arr remove, an AdGuard /
    # Pi-hole disable) MUST carry an explicit confirm flag. Every UI surface
    # confirms BEFORE dispatch — the inline-confirm chip (AI sidebar approval
    # mode), the autonomous-mode opt-in, the Cmd-K SweetAlert, the fleet-card
    # SweetAlert. This gate stops an un-confirmed AI dispatch from firing a
    # destructive skill if a UI-side gate ever regresses (mirrors the SSH
    # typed-confirm contract). Non-destructive skills are unaffected.
    if skill.get("destructive") and not skill_confirm:
        print(f"[app_skill] warning: web skill {skill_id!r} BLOCKED — destructive "
              f"skill needs confirm=true (host={host_id} svc_idx={service_idx})")
        raise HTTPException(409, f"destructive skill '{skill_id}' requires confirmation")
    # Wall-clock budget UNDER the reverse-proxy proxy_read_timeout (same
    # rationale as the app-data route): a slow skill (e.g. a live status fetch
    # behind a stalled upstream) fails with OmniGrid's OWN logged 504 instead
    # of hanging until the front proxy emits an unlogged gateway 504 with no
    # server trace. Most skills are designed to return immediately (background
    # pattern), so this only bites a genuinely-slow live fetch.
    _budget = float(tuning.tuning_int(Tunable.APPS_ROUTE_BUDGET_SECONDS))
    try:
        result = await asyncio.wait_for(
            mod.run_skill(skill_id, host_row, chip,
                          host_id=host_id, service_idx=service_idx,
                          arg=skill_arg,
                          actor_username=_actor_from(request)),
            timeout=_budget)
    except asyncio.TimeoutError:
        print(f"[app_skill] error: web skill {skill_id!r} TIMED OUT at "
              f"host={host_id} svc_idx={service_idx} budget={_budget:.0f}s "
              f"(upstream too slow — raised our own 504 before the reverse "
              f"proxy could emit an unlogged gateway timeout)")
        raise HTTPException(
            504, f"skill '{skill_id}' exceeded {_budget:.0f}s budget")
    except ValueError as e:  # unknown skill id reaching the module
        print(f"[app_skill] warning: web skill {skill_id!r} rejected by module at "
              f"host={host_id} svc_idx={service_idx} — {e}")
        raise HTTPException(404, str(e))
    except RuntimeError as e:  # upstream / dispatch failure
        print(f"[app_skill] error: web skill {skill_id!r} dispatch failed at "
              f"host={host_id} svc_idx={service_idx} — {e}")
        result = {"ok": False, "detail": str(e)}
    if not isinstance(result, dict):
        result = {"ok": True}
    print(f"[app_skill] INFO web skill {skill_id!r} host={host_id} svc_idx={service_idx} "
          f"-> ok={result.get('ok')} detail={result.get('detail')}")
    with db_conn() as _c:
        _ops_mod.write_admin_audit(
            _c, "services_skill",
            target_kind="host", target_name=host_id, target_id=host_id,
            actor=_actor_from(request),
            message=(f"Ran skill '{skill_id}' on service_idx={service_idx} "
                     f"of {host_id} (ok={result.get('ok')})"),
        )
    return result


@app.delete("/api/services/{host_id}/{service_idx}")
async def api_service_unpin(host_id: str, service_idx: int, request: Request, _admin: AdminUser):
    """Admin-only: remove (unpin) one chip from a host's services[].

    Note this re-indexes the host's remaining chips (service_idx is a
    positional index), so the SPA must re-fetch the instance list after
    a delete. Persists through the shared validated choke point."""
    hosts = _load_hosts_config()
    target_idx = _find_host_idx(hosts, host_id)
    if target_idx < 0:
        raise HTTPException(404, f"host not found: {host_id}")
    chips = hosts[target_idx].get("services") or []
    if not isinstance(chips, list) or service_idx < 0 or service_idx >= len(chips):
        raise HTTPException(404, f"service_idx {service_idx} out of range for host {host_id}")
    removed = chips.pop(service_idx)
    removed_name = (removed.get("name") if isinstance(removed, dict) else None) or f"service_idx={service_idx}"
    _persist_host_services(hosts, target_idx, chips)
    with db_conn() as _c:
        _ops_mod.write_admin_audit(
            _c, "services_unpin",
            target_kind="host", target_name=host_id, target_id=host_id,
            actor=_actor_from(request),
            message=f"Unpinned app chip '{removed_name}' from {host_id}",
        )
    return {"ok": True, "host_id": host_id, "service_idx": service_idx}


@app.get("/api/apps")
async def api_apps_list(_admin: AdminUser, force: bool = False):
    """Admin-only: cross-host aggregate view. Returns one row per
    distinct app (grouped by catalog_id or name) with every host that
    runs an instance + per-instance status.

    Served from a short-TTL (8s) cache in `list_apps()` so a burst of
    page loads / polls doesn't re-run the heavy `service_samples`
    queries; `?force=true` bypasses the cache for an explicit operator
    Refresh.

    OFFLOADED to a worker thread via `asyncio.to_thread`: `list_apps()`
    is synchronous SQLite. The per-host fan-out it once did (a fresh
    `db_conn()` per host × 3 queries each) has been BATCHED into THREE
    fleet-wide windowed queries — `latest_for_hosts` /
    `latest_per_port_all_for_hosts` / `history_rollup_all_for_hosts`
    (each ONE `ROW_NUMBER()` SELECT over the whole curated-host set, in
    `logic/service_sampler.py`), so the per-host round-trip count went
    from hundreds to three. The `to_thread` offload stays anyway because
    even three large windowed SELECTs are synchronous SQLite that would
    block the event loop — wedging `/api/healthz` past the 20s Docker
    healthcheck → Swarm SIGKILL → crash-loop (the original
    operator-flagged "apps page loads forever → HTTP 504" symptom, the
    proxy timing out on the synchronously-blocked loop). Same offload
    pattern as `host_baseline_sampler`. The 8s `_LIST_APPS_CACHE` module
    cache + the batched queries + the offload are complementary, not
    redundant — caching avoids re-running, batching cuts the per-call
    cost, offloading keeps even the batched call off the event loop.
    """
    from logic import service_catalog as _sc
    apps = await asyncio.to_thread(_sc.list_apps, force)
    return {"apps": apps}


# Release-calendar widget fan-out — in-process cache keyed by the date window
# (short TTL so a burst of widget polls / month re-renders doesn't re-hit every
# *arr). Bounded as the user navigates months.
_ARR_CAL_CACHE: "dict[str, tuple[float, dict]]" = {}
_ARR_CAL_TTL_S = 60.0


# The fan-out itself (which *arr slugs, the per-instance calendar_items merge)
# lives in the shared ``logic/apps/arr_calendar.py`` aggregator, consumed by
# both this widget route AND the AI palette's upcoming_releases tool.
@app.get("/api/apps/arr-calendar")
async def api_apps_arr_calendar(_admin: AdminUser, start: str = "", end: str = ""):
    """Admin-only: upcoming-release calendar across every CONFIGURED Radarr /
    Sonarr / Lidarr / Readarr instance (a pinned chip with an api_key set), for
    the ``[start, end]`` date window (``YYYY-MM-DD`` inclusive). Returns the
    merged, normalised items + which services contributed. Each item carries
    ``host_id`` + ``service_idx`` so the SPA routes its poster through the
    per-app image proxy. Powers the Apps custom-dashboard ``arr_calendar``
    widget.

    Category gating is structural — a service with no configured instance simply
    contributes nothing (Radarr off → no movies). ``configured`` is False when
    NO *arr instance is set up at all (the widget then shows its 'configure an
    *arr service' empty state). The whole widget is additionally hidden from the
    Add-widget picker via ``/api/me``'s ``client_config.arr_calendar_available``.
    """
    import time  # noqa: PLC0415
    from datetime import datetime, timedelta, timezone  # noqa: PLC0415

    def _valid_ymd(s: str) -> bool:
        try:
            datetime.strptime(s, "%Y-%m-%d")
            return True
        except (ValueError, TypeError):
            return False

    # Default the window to the current month's ~6-week grid when unset / bad.
    today = datetime.now(timezone.utc).date()
    if not _valid_ymd(start or ""):
        start = (today.replace(day=1) - timedelta(days=7)).strftime("%Y-%m-%d")
    if not _valid_ymd(end or ""):
        end = (today.replace(day=1) + timedelta(days=44)).strftime("%Y-%m-%d")
    ck = f"{start}|{end}"
    now = time.time()
    cached = _ARR_CAL_CACHE.get(ck)
    if cached and (now - cached[0]) < _ARR_CAL_TTL_S:
        return cached[1]
    start_iso, end_iso = start + "T00:00:00Z", end + "T23:59:59Z"
    # Fan out across every configured *arr instance via the shared aggregator
    # (also consumed by the AI palette's upcoming_releases tool).
    from logic.apps.arr_calendar import collect_calendar  # noqa: PLC0415
    agg = await collect_calendar(start_iso, end_iso)
    items = agg.get("items") or []
    out = {
        "configured": agg.get("configured", False),
        "services": agg.get("services") or [],
        "items": items,
        "count": len(items),
        "errors": agg.get("errors") or {},
        "start": start,
        "end": end,
        "fetched_at": int(now),
    }
    _ARR_CAL_CACHE[ck] = (now, out)
    if len(_ARR_CAL_CACHE) > 24:  # drop the oldest window as months accumulate
        _ARR_CAL_CACHE.pop(min(_ARR_CAL_CACHE.items(), key=lambda kv: kv[1][0])[0], None)
    return out


@app.post("/api/apps/catalog/{slug}/show-extras")
async def api_apps_catalog_set_show_extras(slug: str, payload: dict, _admin: AdminUser):
    """Toggle a catalog template's ``show_extras`` flag by slug. Admin-only.

    PARTIAL update — only the ``show_extras`` column changes (via
    ``update_catalog_entry``'s keyword-only `show_extras`), so the
    template's name / icon / ports / probe config are untouched. Lets the
    operator flip a per-app extras panel (e.g. APC's UPS stats) straight
    from the Apps-page card-settings flip OR from Admin → Apps; both hit
    this one endpoint. 404 when the slug doesn't resolve.
    """
    from logic import service_catalog as _sc
    existing = await asyncio.to_thread(_sc.get_catalog_by_slug, slug)
    if not existing:
        raise HTTPException(status_code=404, detail="template not found")
    value = bool(payload.get("show_extras"))
    updated = await asyncio.to_thread(
        _sc.update_catalog_entry, int(existing["id"]), show_extras=value
    )
    return {"ok": updated is not None, "show_extras": value}


@app.post("/api/apps/tile-trace")
async def api_apps_tile_trace(payload: dict[str, Any], _admin: AdminUser):
    """Admin-only diagnostic sink for the Apps-view per-tile render trace.

    SPA POSTs one row per tile when its body finishes mounting (or when
    the body throws), carrying group_id + slug + elapsed-ms + an optional
    phase / error string. We mirror to container stdout as a single
    ``[apps-tile]`` line so the user can correlate browser-side
    `console.debug` timing with backend logs when chasing a frozen tile.
    No persistence, no SSE — purely a "where did the hang go?" probe.
    Returns ``{ok: true}`` regardless so the SPA's fire-and-forget POST
    is never the reason a page hangs.

    Body shape (every field optional except ``group_id``)::

        {"group_id": "<gid>", "slug": "<catalog-slug>",
         "name": "<display name>", "took_ms": <int>,
         "phase": "mount" | "error" | "first-paint",
         "error": "<text>"}
    """
    try:
        gid = str(payload.get("group_id") or "").strip()
        if not gid:
            return {"ok": False, "error": "missing group_id"}
        slug = str(payload.get("slug") or "").strip() or "?"
        name = str(payload.get("name") or "").strip() or "?"
        phase = str(payload.get("phase") or "mount").strip() or "mount"
        try:
            took = int(payload.get("took_ms") or 0)
        except (TypeError, ValueError):
            took = 0
        err = str(payload.get("error") or "").strip()
        bits = [
            f"[apps-tile] phase={phase}",
            f"gid={gid}",
            f"slug={slug}",
            f"name={name!r}",
            f"took_ms={took}",
        ]
        if err:
            # `warning:` so `_severity_for` routes the line into the
            # WARN bucket without triggering the ERROR classifier
            # (which would mis-report every slow tile as a real
            # error). The token order matters here.
            bits.insert(0, "warning:")
            bits.append(f"error={err!r}")
        print(" ".join(bits), flush=True)
    except (TypeError, ValueError, AttributeError, KeyError):
        # The trace sink is never load-bearing — eat the parse error so
        # a malformed SPA payload can't surface as a noisy 500 in the
        # devtools console (which would in turn re-fire the trace and
        # storm the backend). The catch is intentionally narrow so a
        # genuine bug (asyncio cancellation, real I/O fault) still
        # propagates.
        return {"ok": False}
    return {"ok": True}


@app.post("/api/admin/spa-diagnostic")
async def api_admin_spa_diagnostic(payload: dict[str, Any], _admin: AdminUser):
    """Admin-only diagnostic sink for SPA-side performance probes.

    Generic counterpart to ``/api/apps/tile-trace`` — accepts a
    ``{kind, ...payload}`` body and mirrors to container stdout as a
    single ``[spa-diagnostic]`` line so the user can correlate
    browser-side warnings (rAF violations, long-task observations,
    memory-pressure spikes) against backend logs when devtools isn't
    accessible (page-unresponsive scenarios).

    Current emitters (extend as new probes ship):

    * ``raf-violation`` — `window.requestAnimationFrame` callback
      took longer than the user-set threshold (URL `?raf=<ms>` or
      `window.__ogRafProbeMs`). Body: ``{kind, took_ms, cb, caller, threw}``.

    Returns ``{ok: true}`` regardless — the probe is fire-and-forget
    by contract; a 500 here would re-amplify the page hang the probe
    is trying to debug.
    """
    try:
        kind = str(payload.get("kind") or "?").strip() or "?"
        bits = [f"[spa-diagnostic] kind={kind}"]
        # Whitelist the keys we surface so the line stays readable +
        # an SPA-side typo can't blow up the log format.
        for key in ("took_ms", "cb", "caller", "threw", "url", "detail"):
            if key in payload:
                v = payload.get(key)
                bits.append(f"{key}={v!r}" if isinstance(v, str) else f"{key}={v}")
        # rAF-violation lines carry a `warning:` token so `_severity_for`
        # routes them into the WARN bucket (operator-visible in Admin →
        # Logs without polluting the ERROR bucket reserved for real
        # failures).
        if kind == "raf-violation":
            bits.insert(0, "warning:")
        print(" ".join(bits), flush=True)
    except (TypeError, ValueError, AttributeError, KeyError):
        return {"ok": False}
    return {"ok": True}


@app.get("/api/apps/instances")
async def api_apps_instances(_admin: AdminUser):
    """Admin-only: flat per-instance iterator — every chip across every
    host. Used by the Admin → Apps tab's instance list.

    Same `asyncio.to_thread` offload rationale as `/api/apps` above
    — `iter_instances()` walks every curated host, opens DB reads
    per host, runs synchronously. Without the offload it blocks
    /api/healthz on the same code path that triggered the
    crash-loop. List materialisation runs inside the worker thread
    too so the generator doesn't leak back to the event loop.
    """
    from logic import service_catalog as _sc
    instances = await asyncio.to_thread(lambda: list(_sc.iter_instances()))
    return {"instances": instances}


# noinspection PyProtectedMember
@app.post("/api/services/{host_id}/{service_idx}/probe")
async def api_service_probe_now(host_id: str, service_idx: int, request: Request, _admin: AdminUser):
    """Admin-only: run a one-shot probe against a specific chip and
    persist the result to ``service_samples`` so the SPA picks it up
    on the next refresh. Returns the probe outcome inline so the SPA
    can render the result without waiting for the next API poll.

    Routes through the existing TCP / HTTP probe helpers in
    ``logic.service_sampler`` so the manual path uses the same code
    as the lifespan sampler — one probe verb, one persistence shape,
    one history pipeline.
    """
    import time as _time
    from logic import service_sampler as _ss
    # Resolve the chip from hosts_config.
    hosts = _load_hosts_config()
    target_host = None
    for row in hosts:
        if (row.get("id") or "").strip() == host_id:
            target_host = row
            break
    if target_host is None:
        raise HTTPException(404, f"host not found: {host_id}")
    chips = target_host.get("services") or []
    if not isinstance(chips, list) or service_idx < 0 or service_idx >= len(chips):
        raise HTTPException(404, f"service_idx {service_idx} out of range for host {host_id}")
    chip = chips[service_idx]
    if not isinstance(chip, dict):
        raise HTTPException(400, "service entry malformed")
    # Resolve the probe target via the SAME shared helper the lifespan
    # sampler + Apps debug endpoint use, so the manual probe-now path
    # can't drift from them (the address fallback + multi-port handling
    # all live in one place now). None = unprobeable; re-derive a
    # specific 400 reason since the resolver collapses every skip case
    # to None.
    # require_enabled=False: a manual probe-now is an explicit one-shot
    # opt-in for THIS run, so it works even when the continuous
    # sampler flag (probe.enabled) is off — as long as the chip has a
    # resolvable target (configured ports / port / URL + host address).
    tgt = _ss.resolve_chip_probe_target(target_host, service_idx, chip, require_enabled=False)
    if tgt is None:
        if not ((chip.get("url") or "").strip() or (target_host.get("address") or "").strip()):
            raise HTTPException(400, "unable to resolve probe target host — set the chip URL or the host Address")
        raise HTTPException(400, "no probe port resolvable; set chip url, probe.port, or probe.ports[]")
    probe_type = tgt["probe_type"]
    url = tgt["url"]
    parsed_host = tgt["host"]
    port = tgt["port"]
    expected_status = tgt["expected_status"]
    # `probe.ports[]` (multi-port) is resolved into sub_ports by the
    # shared helper; when set, the legacy single-port `probe.port` is
    # ignored — same shape as the sampler so manual + scheduled paths
    # persist identically.
    sub_ports = tgt["sub_ports"]
    timeout_s = float(tuning.tuning_int(Tunable.SERVICE_PROBE_TIMEOUT_SECONDS))
    ts = int(_time.time())
    port_results: list[dict] = []
    if sub_ports:
        any_alive = False
        min_rtt: Optional[int] = None
        first_error = None
        for sp in sub_ports:
            # `sp_port` / `sp_status` narrow from dict-Any access to
            # concrete ints so the downstream `probe_tcp` / persistence
            # signatures don't flag Any|None on every call.
            sp_port = _coerce_int_local(sp.get("port")) or 0
            sp_status = _coerce_int_local(sp.get("probe_status")) or 0
            if sp["probe_type"] == "http":
                scheme = "https" if sp["protocol"] == "https" else "http"
                sub_url = f"{scheme}://{parsed_host}:{sp_port}{sp['probe_path']}"
                r = await _ss.probe_http(sub_url, sp_status, timeout_s)
            elif sp["probe_type"] == "udp":
                # Real UDP probe via the protocol-correct path —
                # MUST mirror the sampler's UDP branch at
                # `logic/service_sampler.py:452-470` so the manual
                # probe-now endpoint and the lifespan sampler produce
                # IDENTICAL outcomes for the same chip. Pre-fix this
                # branch was missing — UDP fell into the TCP `else`
                # below → port 161 (SNMP / NTP / etc.) TCP-connected
                # → returned `ConnectionRefusedError: [Errno 111]`
                # because the service is UDP-only and nothing's
                # listening on TCP/161. Operator-flagged with the
                # APC chip's SNMP port: the sampler tick correctly
                # reported "udp: no response (open|filtered)" but
                # the Refresh button replaced it with a
                # ConnectionRefused that misled diagnosis. Same
                # shape + fallback message as the sampler.
                from logic.port_scanner_udp import _probe_one_udp as _udp_probe
                _udp_out = await _udp_probe(parsed_host, sp_port, timeout_s)
                r = {
                    "alive": bool(_udp_out.get("open")),
                    "rtt_ms": None,
                    "error": None if _udp_out.get("open")
                    else "udp: no response (open|filtered — can't confirm)",
                }
            else:
                r = await _ss.probe_tcp(parsed_host, sp_port, timeout_s)
            r_rtt = _coerce_int_local(r.get("rtt_ms"))
            # `r.get("error")` is `Any` (dict value) — narrow to
            # Optional[str] so `persist_row(error=Optional[str])`'s
            # signature matches without an Any|None|bool fallthrough
            # that Pyright flags at the call site below.
            _r_error_raw = r.get("error")
            r_error: Optional[str] = _r_error_raw if isinstance(_r_error_raw, str) else None
            pr = {"port": sp_port, "label": sp["label"],
                  "alive": bool(r.get("alive")), "rtt_ms": r_rtt,
                  "error": r_error}
            port_results.append(pr)
            # Per-port row persistence.
            _ss.persist_row(host_id, service_idx,
                            bool(r.get("alive")), r_rtt,
                            r_error, ts, port=sp_port)
            if r.get("alive"):
                any_alive = True
                if r_rtt is not None and (min_rtt is None or r_rtt < min_rtt):
                    min_rtt = r_rtt
            elif first_error is None:
                first_error = r.get("error")
        result = {"alive": any_alive, "rtt_ms": min_rtt,
                  "error": None if any_alive else (first_error or "all ports down")}
    elif probe_type == "http" and url:
        result = await _ss.probe_http(url, expected_status, timeout_s)
    else:
        result = await _ss.probe_tcp(parsed_host, int(port or 0), timeout_s)
    # Rollup row (port=0) — always written so the chip-level status
    # updates regardless of single-port vs multi-port shape. The
    # explicit `port=0` matches the sentinel-rollup contract; spelling
    # it out at every call site beats relying on the default.
    # Narrow `rtt_ms` + `error` from the result dict's Any-typed cells
    # to concrete Optional[int] / Optional[str] so persist_row's
    # parameter types match without an Any|None fallthrough.
    result_rtt = _coerce_int_local(result.get("rtt_ms"))
    result_error_raw = result.get("error")
    result_error = result_error_raw if isinstance(result_error_raw, str) else None
    # noinspection PyArgumentEqualDefault
    _ss.persist_row(
        host_id, service_idx,
        bool(result.get("alive")),
        result_rtt,
        result_error,
        ts,
        port=0,
    )
    # Audit row — manual probe-now is a tracked operator action even
    # though sampler-driven probes write nothing. Per the the project conventions
    # audit-trail rule each operator-initiated write needs a history
    # entry; the lifespan sampler's higher-volume background probes
    # remain intentionally unaudited.
    with db_conn() as _c:
        _ops_mod.write_admin_audit(
            _c, "services_probe_now",
            target_kind="host",
            target_name=host_id,
            target_id=host_id,
            actor=_actor_from(request),
            status="success" if result.get("alive") else "error",
            message=(f"Manual probe service_idx={service_idx} on {host_id}: "
                     + ("alive" if result.get("alive") else "down")
                     + (f" ({result_rtt}ms)" if result_rtt is not None else "")),
            error=result_error,
        )
    return {
        "ok": True,
        "host_id": host_id,
        "service_idx": service_idx,
        "ts": ts,
        "alive": bool(result.get("alive")),
        "rtt_ms": result.get("rtt_ms"),
        "error": result.get("error"),
        # Per-port detail — empty list for single-port chips.
        "port_results": port_results,
    }


# noinspection DuplicatedCode
@app.get("/api/services/{host_id}/{service_idx}/debug")
async def api_service_debug(host_id: str, service_idx: int, _admin: AdminUser):
    """Admin-only: full diagnostic for ONE app instance.

    Surfaces exactly what the lifespan sampler would resolve as the
    probe target(s) for this host+chip, the chip + catalog config, and
    the latest per-port outcomes — so an operator can see WHY a given
    app on a given host isn't reporting (probe disabled, no resolvable
    target, wrong port, connection refused, unexpected status, …)
    without reading the sampler source. Powers the Apps detail drawer's
    debug panel (mirrors the host drawer's debug panel)."""
    from logic import service_sampler as _ss
    from logic import service_catalog as _sc
    from logic.db import get_setting_bool
    hosts = _load_hosts_config()
    target_host = None
    for row in hosts:
        if (row.get("id") or "").strip() == host_id:
            target_host = row
            break
    if target_host is None:
        raise HTTPException(404, f"host not found: {host_id}")
    chips = target_host.get("services") or []
    if not isinstance(chips, list) or service_idx < 0 or service_idx >= len(chips):
        raise HTTPException(404, f"service_idx {service_idx} out of range for host {host_id}")
    chip = chips[service_idx] if isinstance(chips[service_idx], dict) else {}
    probe_cfg = chip.get("probe") or {}
    host_address = (target_host.get("address") or "").strip()

    # Resolved targets — exactly what the sampler's target builder
    # produces for THIS host+idx, so the operator sees the true probe
    # host:port / URL + path rather than guessing.
    resolved: list[dict[str, Any]] = []
    probe_reason = ""
    try:
        # noinspection PyProtectedMember
        # _curated_service_probe_targets is the shared sampler helper reused
        # cross-module by design (no public alias) — same convention as the
        # other underscore helpers noted in this module's header.
        for tgt in _ss._curated_service_probe_targets():
            tgt_idx = int(tgt.get("service_idx", -1))
            if tgt.get("host_id") == host_id and tgt_idx == service_idx:
                subs = tgt.get("sub_ports") or []
                if subs:
                    for sp in subs:
                        is_http = sp.get("probe_type") == "http"
                        scheme = "https" if sp.get("protocol") == "https" else "http"
                        resolved.append({
                            "port": sp.get("port"),
                            "protocol": sp.get("protocol"),
                            "probe_type": sp.get("probe_type"),
                            "probe_path": sp.get("probe_path") if is_http else "",
                            "expected_status": sp.get("probe_status") or 0,
                            "target": (f"{scheme}://{tgt['host']}:{sp['port']}{sp.get('probe_path') or '/'}"
                                       if is_http else f"{tgt['host']}:{sp['port']}"),
                        })
                else:
                    is_http = tgt.get("probe_type") == "http"
                    resolved.append({
                        "port": tgt.get("port"),
                        "protocol": "http" if is_http else "tcp",
                        "probe_type": tgt.get("probe_type"),
                        "probe_path": tgt.get("path") if is_http else "",
                        "expected_status": tgt.get("expected_status") or 0,
                        "target": (tgt.get("url") or f"{tgt['host']}:{tgt.get('port')}"),
                    })
                break
    except Exception as e:  # noqa: BLE001
        probe_reason = f"target resolution error: {e}"

    # No resolved target → explain why (the common operator confusion:
    # a catalog-pinned chip with neither a URL nor a host Address, or a
    # disabled probe).
    if not resolved and not probe_reason:
        if not probe_cfg.get("enabled"):
            probe_reason = "probe is disabled for this chip"
        elif not ((chip.get("url") or "").strip() or host_address):
            probe_reason = ("no probe target — chip has no URL and the host has no "
                            "Address set (Admin → Hosts)")
        else:
            probe_reason = ("no probe port resolvable — set probe.ports[] or a chip "
                            "URL that includes a port")

    catalog = None
    cid = _coerce_int_local(chip.get("catalog_id"))
    if cid is not None:
        catalog = _sc.get_catalog_by_id(cid)

    # Continuous-sampler eligibility — distinct from per-target
    # resolution. The lifespan sampler only LIVE-probes a chip when the
    # GLOBAL Service-probe provider is enabled AND the chip's own
    # probe.enabled is on. Either being off means the port pills stay
    # grey/pending even though the per-app "Probe now" button (a one-shot
    # that bypasses both gates) still works. Surfacing both here so the
    # operator can see — and share from this panel — why an app reads
    # "not probing" without checking logs.
    master_enabled = get_setting_bool(Settings.SERVICE_PROBE_ENABLED)
    live_probe_blockers: list[str] = []
    if not master_enabled:
        live_probe_blockers.append(
            "Service probe provider is globally OFF — enable it in Admin → Providers → Service probe")
    if not probe_cfg.get("enabled"):
        live_probe_blockers.append(
            "this chip's probe is disabled (probe.enabled = false)")

    return {
        "host_id": host_id,
        "host_label": (target_host.get("label") or host_id).strip(),
        "host_address": host_address,
        "service_idx": service_idx,
        # Continuous-probe gates (master provider toggle + per-chip flag)
        # + a human blocker list. live_probe_blockers empty == continuous
        # probing is eligible for this chip; the one-shot "Probe now"
        # button works regardless of these.
        "service_probe_master_enabled": master_enabled,
        "live_probe_blockers": live_probe_blockers,
        "chip": {
            "name": (chip.get("name") or "").strip(),
            "catalog_id": cid,
            "url": (chip.get("url") or "").strip(),
            "icon": (chip.get("icon") or "").strip(),
            "probe": {
                "enabled": bool(probe_cfg.get("enabled")),
                "type": (probe_cfg.get("type") or "tcp"),
                "port": probe_cfg.get("port"),
                "path": probe_cfg.get("path") or "",
                "ports": probe_cfg.get("ports") or [],
            },
        },
        "catalog": catalog,
        "resolved_targets": resolved,
        "probe_reason": probe_reason,
        "rollup": _ss.latest_for_host(host_id).get(service_idx),
        "port_results": _ss.latest_per_port_for_host(host_id, service_idx),
    }


def _demux_docker_logs(raw: bytes) -> str:
    """Decode a Docker ``/containers/{id}/logs`` body to plain text.

    Non-TTY containers return a MULTIPLEXED stream: each frame is an
    8-byte header ``[stream(1), 0, 0, 0, size(4, big-endian)]`` followed
    by ``size`` payload bytes. TTY containers return plain text with no
    header. Detect by the first byte: a header's stream byte is 0/1/2, so
    if it isn't we treat the whole buffer as plain text; otherwise we walk
    frames, stripping headers. Any structural surprise falls back to a
    best-effort full decode rather than raising."""
    if not raw:
        return ""
    # decode(errors="replace") — encoding defaults to utf-8; int.from_bytes
    # defaults to big-endian. Both defaults spelled implicitly so the
    # linter doesn't flag the (redundant) explicit "utf-8" / "big".
    try:
        if raw[0] not in (0, 1, 2):
            return raw.decode(errors="replace")
        out: list[str] = []
        i = 0
        n = len(raw)
        while i + 8 <= n:
            if raw[i] not in (0, 1, 2):
                out.append(raw[i:].decode(errors="replace"))
                i = n
                break
            size = int.from_bytes(raw[i + 4:i + 8])
            i += 8
            out.append(raw[i:i + size].decode(errors="replace"))
            i += size
        if i < n:
            out.append(raw[i:].decode(errors="replace"))
        return "".join(out)
    except (ValueError, IndexError, UnicodeError):
        return raw.decode(errors="replace")


# Container ref must be hex ID or a Docker name (alnum + _ . -). Guards the
# value before it's interpolated into the outbound Portainer URL (CodeQL
# path-injection / SSRF discipline) + the agent-target hostname.
_CONTAINER_REF_RE = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_AGENT_NODE_RE = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@app.get("/api/container/{raw_id}/logs")
async def api_container_logs(raw_id: str, _admin: AdminUser,
                             tail: int = 200, node: str = ""):
    """Admin-only: tail a container's logs via Portainer. Powers the App
    detail drawer's Docker-link "Logs" action. Threads
    ``X-PortainerAgent-Target`` so worker-node containers resolve, and
    demuxes Docker's multiplexed log stream to plain text. Read-only — no
    history audit (matches the per-host debug / stats reads)."""
    import httpx
    from logic import portainer
    if not _CONTAINER_REF_RE.match(raw_id or ""):
        raise HTTPException(400, "invalid container ref")
    node_clean = (node or "").strip()
    if node_clean and not _AGENT_NODE_RE.match(node_clean):
        raise HTTPException(400, "invalid node")
    try:
        tail_n = max(1, min(2000, int(tail)))
    except (TypeError, ValueError):
        tail_n = 200
    eid = portainer.PORTAINER_ENDPOINT_ID
    url = (f"{portainer.PORTAINER_URL}/api/endpoints/{eid}/docker/containers/"
           f"{raw_id}/logs?stdout=1&stderr=1&timestamps=1&tail={tail_n}")
    try:
        async with portainer.write_client(timeout=20.0) as client:
            r = await client.get(url, headers=portainer.headers(agent_target=node_clean or None))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        raise HTTPException(502, f"Portainer logs fetch failed: {e}")
    if r.status_code == 404:
        raise HTTPException(404, "container not found")
    if r.status_code >= 400:
        raise HTTPException(502, f"Portainer HTTP {r.status_code}: {r.text[:300]}")
    text = _demux_docker_logs(r.content)
    # Defensive size cap — keep the tail end if a chatty container blew
    # past the line budget with very long lines.
    if len(text) > 200000:
        text = text[-200000:]
    return {"raw_id": raw_id, "node": node_clean, "tail": tail_n, "logs": text}


# noinspection DuplicatedCode
@app.get("/api/service/{raw_id}/logs")
async def api_service_logs(raw_id: str, _admin: AdminUser, tail: int = 200):
    """Admin-only: tail a Swarm SERVICE's logs via Portainer (the
    ``docker service logs`` aggregate). Powers the App-drawer Docker-link
    "Logs" action for SERVICE links (``docker_stack``) — a service spans
    N task containers across hosts, so Docker interleaves their
    stdout/stderr into one stream. No ``X-PortainerAgent-Target``: the
    service-logs API is a manager-level Swarm call, not node-scoped.
    Demuxes the multiplexed stream like the container path. Read-only —
    no history audit (matches the container-logs read)."""
    import httpx
    from logic import portainer
    if not _CONTAINER_REF_RE.match(raw_id or ""):
        raise HTTPException(400, "invalid service ref")
    try:
        tail_n = max(1, min(2000, int(tail)))
    except (TypeError, ValueError):
        tail_n = 200
    eid = portainer.PORTAINER_ENDPOINT_ID
    url = (f"{portainer.PORTAINER_URL}/api/endpoints/{eid}/docker/services/"
           f"{raw_id}/logs?stdout=1&stderr=1&timestamps=1&tail={tail_n}")
    try:
        async with portainer.write_client(timeout=20.0) as client:
            r = await client.get(url, headers=portainer.headers())
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        raise HTTPException(502, f"Portainer service-logs fetch failed: {e}")
    if r.status_code == 404:
        raise HTTPException(404, "service not found")
    if r.status_code >= 400:
        raise HTTPException(502, f"Portainer HTTP {r.status_code}: {r.text[:300]}")
    text = _demux_docker_logs(r.content)
    if len(text) > 200000:
        text = text[-200000:]
    return {"raw_id": raw_id, "tail": tail_n, "logs": text}


@app.get("/api/services/{host_id}/{service_idx}/history")
async def api_service_history(host_id: str, service_idx: int, *,
                              hours: int = 24,
                              port: Optional[int] = None,
                              _admin: AdminUser):
    """Admin-only: per-(host, service_idx) probe history for the host
    drawer's Apps sub-tab. Returns up to N hours of samples ordered
    oldest-first so a sparkline can render directly.

    ``port`` query param filters the returned rows:
      - omitted (None): returns the ROLLUP series (chip-level any-port-up
        history) — equivalent to ``port=0``.
      - 0: explicit rollup-only.
      - >0: per-port history for that specific port number.
      - -1: return every row (rollup + every per-port) — useful for the
        Apps drawer's expanded multi-port chart.
    """
    import time as _time
    hours_clamped = max(1, min(int(hours or 24), 24 * 30))
    cutoff = int(_time.time() - hours_clamped * 3600)
    # Resolve port filter — None defaults to rollup (port=0).
    if port is None:
        port_filter: Optional[int] = 0
    elif port == -1:
        port_filter = None  # No filter — every row
    else:
        port_filter = int(port)
    base_sql = ("SELECT ts, alive, rtt_ms, error, port "
                "FROM service_samples "
                "WHERE host_id = ? AND service_idx = ? AND ts >= ?")
    params: list[Any] = [host_id, service_idx, cutoff]
    if port_filter is not None:
        base_sql += " AND port = ?"
        params.append(port_filter)
    base_sql += " ORDER BY ts ASC, port ASC"
    try:
        with db_conn() as c:
            rows = c.execute(base_sql, tuple(params)).fetchall()
    except sqlite3.OperationalError as oe:
        # Pre-migration-005 schemas don't have the `port` column yet;
        # fall back to a SELECT without it so the endpoint stays useful
        # on a database that hasn't run the additive ALTER.
        if "no such column" in str(oe).lower():
            with db_conn() as c:
                rows = c.execute(
                    "SELECT ts, alive, rtt_ms, error, 0 AS port "
                    "FROM service_samples "
                    "WHERE host_id = ? AND service_idx = ? AND ts >= ? "
                    "ORDER BY ts ASC",
                    (host_id, service_idx, cutoff),
                ).fetchall()
        else:
            raise
    return {
        "samples": [
            {
                "ts": int(r[0]),
                "alive": bool(r[1]),
                "rtt_ms": r[2],
                "error": r[3],
                "port": r[4],
                # Self-describing grouping marker so the SPA renderer
                # doesn't have to inspect each row's `port` field to
                # tell rollup (chip-level) from per-port detail when
                # `port_filter=-1` (every row) returns both shapes
                # interleaved. `chip` ↔ port=0 ↔ rollup row;
                # `port` ↔ port>0 ↔ per-port detail.
                "grouping": "chip" if (r[4] or 0) == 0 else "port",
            }
            for r in rows
        ],
        "hours": hours_clamped,
        "port_filter": port_filter,  # None = every row; 0 = rollup; >0 = that port
    }


# ── Hosts merge/shape pipeline extracted to main_pkg.hosts_merge_routes ─
# Star-import re-exports every symbol (incl. its FastAPI routes) so they
# register here in the chain, before auth_routes mounts the static catch-all.
from main_pkg.hosts_merge_routes import *  # noqa: E402,F401,F403

# ── Apps custom-dashboard "views" — shareable named dashboards ──────────────
#
# Views moved out of per-user ``ui_prefs`` into the cross-user ``app_views``
# table (logic/schema.py:init_db) so a view can be made PUBLIC — readable, and
# optionally writable, by OTHER users. Every view is owned by its creator; the
# owner is the only one who can delete it or change its sharing settings.
# Other users' PRIVATE views are never exposed — not even to admins.
#
# Permission model (resolved by ``_app_view_perms``):
#   is_owner   — the creator.
#   can_view   — owner, OR any signed-in user when the view is public.
#   can_edit   — owner, OR a public view with edit_permission='all' AND the
#                caller is NOT a global read-only-role user.
#   can_manage — owner ONLY (delete + change visibility / edit_permission).

_APP_VIEW_COLS = (
    "id, owner_username, name, layout, visibility, "
    "edit_permission, created_at, updated_at"
)
_APP_VIEW_VISIBILITY = ("private", "public")
_APP_VIEW_EDIT_PERM = ("owner", "all")
_APP_VIEW_NAME_MAX = 64
_APP_VIEW_LAYOUT_MAX_BYTES = 256 * 1024  # reject pathologically large layouts


def _mint_app_view_id() -> str:
    """Server-side fallback id when the client doesn't supply one — mirrors
    the SPA's ``view-<token>`` shape."""
    return "view-" + os.urandom(8).hex()


def _app_view_perms(row, user) -> dict:
    """Resolve the caller's rights on one ``app_views`` row. See the module
    banner above for the four flags' semantics."""
    is_owner = (row["owner_username"] or "") == (user.username or "")
    is_public = row["visibility"] == "public"
    can_edit = is_owner or (
        is_public
        and row["edit_permission"] == "all"
        and getattr(user, "role", "") != "readonly"
    )
    return {
        "is_owner": is_owner,
        "can_view": is_owner or is_public,
        "can_edit": can_edit,
        "can_manage": is_owner,
    }


def _shape_app_view(row, user) -> dict:
    """API shape for one ``app_views`` row + the caller's resolved rights."""
    perms = _app_view_perms(row, user)
    try:
        layout = json.loads(row["layout"] or "{}")
    except (ValueError, TypeError):
        layout = {}
    return {
        "id": row["id"],
        "name": row["name"],
        "layout": layout,
        "visibility": row["visibility"],
        "edit_permission": row["edit_permission"],
        "owner_username": row["owner_username"],
        "is_owner": perms["is_owner"],
        "can_edit": perms["can_edit"],
        "can_manage": perms["can_manage"],
        "updated_at": row["updated_at"],
    }


def _clean_app_view_name(raw, fallback: str = "View") -> str:
    """Sanitise an app-view name to a trimmed non-empty string (falls back to ``fallback``)."""
    nm = (raw or "").strip() if isinstance(raw, str) else ""
    if not nm:
        nm = fallback
    return nm[:_APP_VIEW_NAME_MAX]


def _clean_app_view_layout(raw) -> str:
    """Validate + compactly serialize a layout dict; 400 if invalid, 413 if
    pathologically large."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="layout must be an object")
    try:
        blob = json.dumps(raw, separators=(",", ":"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="layout is not JSON-serialisable")
    if len(blob.encode()) > _APP_VIEW_LAYOUT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="layout too large")
    return blob


def _validate_app_view_visibility(v) -> None:
    """Validate an app-view visibility value (``private`` / ``public``); raises 400 otherwise."""
    if v not in _APP_VIEW_VISIBILITY:
        raise HTTPException(status_code=400, detail="visibility must be 'private' or 'public'")


def _validate_app_view_edit_perm(p) -> None:
    """Validate an app-view edit-permission value (``owner`` / ``all``); raises 400 otherwise."""
    if p not in _APP_VIEW_EDIT_PERM:
        raise HTTPException(status_code=400, detail="edit_permission must be 'owner' or 'all'")


@app.get("/api/apps/views")
async def api_app_views_list(_user: CurrentUser):
    """List the app-dashboard views visible to the caller: their OWN (any
    visibility) PLUS every public view. Other users' private views are never
    returned — not even to admins (the WHERE clause excludes them)."""
    me = _user.username or ""
    with db_conn() as c:
        rows = c.execute(
            f"SELECT {_APP_VIEW_COLS} FROM app_views "
            "WHERE owner_username = ? OR visibility = 'public' "
            "ORDER BY (owner_username = ?) DESC, name COLLATE NOCASE ASC",
            (me, me),
        ).fetchall()
    return {"views": [_shape_app_view(r, _user) for r in rows]}


@app.post("/api/apps/views")
async def api_app_views_create(payload: dict[str, Any], _user: CurrentUser):
    """Create a view owned by the caller. Also the path the SPA uses to
    migrate a user's pre-existing local (ui_prefs) views into owned-private
    rows on first load — idempotent on a same-owner re-POST of the same id."""
    me = _user.username or ""
    vid = payload.get("id")
    vid = vid.strip()[:80] if (isinstance(vid, str) and vid.strip()) else _mint_app_view_id()
    name = _clean_app_view_name(payload.get("name"))
    layout_blob = _clean_app_view_layout(payload.get("layout"))
    visibility = payload.get("visibility") or "private"
    edit_perm = payload.get("edit_permission") or "owner"
    _validate_app_view_visibility(visibility)
    _validate_app_view_edit_perm(edit_perm)
    now = int(time.time())
    with db_conn() as c:
        existing = c.execute(
            "SELECT owner_username FROM app_views WHERE id = ?", (vid,)
        ).fetchone()
        if existing is not None and (existing["owner_username"] or "") != me:
            raise HTTPException(status_code=409, detail="view id already exists")
        if existing is None:
            c.execute(
                f"INSERT INTO app_views ({_APP_VIEW_COLS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (vid, me, name, layout_blob, visibility, edit_perm, now, now),
            )
        else:
            # Same-owner re-POST → refresh metadata, preserve created_at.
            c.execute(
                "UPDATE app_views SET name = ?, layout = ?, visibility = ?, "
                "edit_permission = ?, updated_at = ? WHERE id = ?",
                (name, layout_blob, visibility, edit_perm, now, vid),
            )
        _ops_mod.write_admin_audit(
            c, "app_view_create",
            target_kind="app_view", target_id=vid, target_name=name, actor=me,
        )
        row = c.execute(
            f"SELECT {_APP_VIEW_COLS} FROM app_views WHERE id = ?", (vid,)
        ).fetchone()
    return {"view": _shape_app_view(row, _user)}


@app.put("/api/apps/views/{view_id}")
async def api_app_views_update(view_id: str, payload: dict[str, Any], _user: CurrentUser):
    """Update a view. ``name`` / ``layout`` require edit rights (owner, or a
    public 'all'-editable view for non-readonly users); ``visibility`` /
    ``edit_permission`` require ownership."""
    with db_conn() as c:
        row = c.execute(
            f"SELECT {_APP_VIEW_COLS} FROM app_views WHERE id = ?", (view_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="view not found")
        perms = _app_view_perms(row, _user)
        if not perms["can_view"]:
            # Don't reveal the existence of another user's private view.
            raise HTTPException(status_code=404, detail="view not found")
        touches_sharing = ("visibility" in payload) or ("edit_permission" in payload)
        touches_content = ("name" in payload) or ("layout" in payload)
        if touches_sharing and not perms["can_manage"]:
            raise HTTPException(status_code=403, detail="only the owner can change sharing settings")
        if touches_content and not perms["can_edit"]:
            raise HTTPException(status_code=403, detail="you don't have edit access to this view")

        sets, args = [], []
        if "name" in payload:
            sets.append("name = ?")
            args.append(_clean_app_view_name(payload.get("name"), fallback=row["name"]))
        if "layout" in payload:
            sets.append("layout = ?")
            args.append(_clean_app_view_layout(payload.get("layout")))
        if "visibility" in payload:
            _validate_app_view_visibility(payload.get("visibility"))
            sets.append("visibility = ?")
            args.append(payload.get("visibility"))
        if "edit_permission" in payload:
            _validate_app_view_edit_perm(payload.get("edit_permission"))
            sets.append("edit_permission = ?")
            args.append(payload.get("edit_permission"))
        if not sets:
            return {"view": _shape_app_view(row, _user)}
        sets.append("updated_at = ?")
        args.append(int(time.time()))
        args.append(view_id)
        # Every column name in `sets` is a controlled literal — no injection.
        c.execute(f"UPDATE app_views SET {', '.join(sets)} WHERE id = ?", args)
        _ops_mod.write_admin_audit(
            c, "app_view_update",
            target_kind="app_view", target_id=view_id,
            target_name=row["name"], actor=_user.username or "",
        )
        new_row = c.execute(
            f"SELECT {_APP_VIEW_COLS} FROM app_views WHERE id = ?", (view_id,)
        ).fetchone()
    return {"view": _shape_app_view(new_row, _user)}


# noinspection DuplicatedCode
@app.delete("/api/apps/views/{view_id}")
async def api_app_views_delete(view_id: str, _user: CurrentUser):
    """Delete a view. Owner only."""
    with db_conn() as c:
        row = c.execute(
            f"SELECT {_APP_VIEW_COLS} FROM app_views WHERE id = ?", (view_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="view not found")
        perms = _app_view_perms(row, _user)
        if not perms["can_view"]:
            raise HTTPException(status_code=404, detail="view not found")
        if not perms["can_manage"]:
            raise HTTPException(status_code=403, detail="only the owner can delete this view")
        c.execute("DELETE FROM app_views WHERE id = ?", (view_id,))
        _ops_mod.write_admin_audit(
            c, "app_view_delete",
            target_kind="app_view", target_id=view_id,
            target_name=row["name"], actor=_user.username or "",
        )
    return {"ok": True}


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
