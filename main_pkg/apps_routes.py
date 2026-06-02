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
import asyncio
import hashlib
import json
import os
import sqlite3
import time
from typing import Any, Iterable, Optional

# IDE contract: PyCharm/Pyright can't trace `from X import *`, so the
# wildcard above leaves every resolved name flagged as "Unresolved
# reference" — including names referenced inside nested function /
# closure scopes (TYPE_CHECKING-block imports DON'T propagate into
# those for PyCharm). The explicit imports below resolve at runtime
# too (main's body has already defined these symbols by the time this
# module is loaded from main's tail star-import chain), so they're a
# safe no-op runtime-wise + a full silencing of the IDE.
from main import (  # noqa: E402,F401  — re-imports for IDE static-analysis
    AdminUser,
    HTTPException,
    Request,
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
    db_conn,
    get_setting,
    set_setting,
    tuning,
)
# Hoisted top-level import for the canonical per-(provider, host)
# outcome recorder. Safe at module top because `host_metrics_sampler`
# does NOT import from `apps_routes` (one-way dep graph) — no cycle.
# Previously this was imported inside ~10 inner-function branches per
# `_merge_one_host` call body (`from logic.host_metrics_sampler import
# record_provider_outcome` repeated verbatim), wasted overhead + noise.
from logic.host_metrics_sampler import (  # noqa: E402
    record_provider_outcome as _record_provider_outcome,
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
    try:
        result = await mod.test_credential(host_row, chip, candidate_key)
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
    return result


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
    try:
        return await mod.fetch_data(host_row, chip,
                                    host_id=host_id, service_idx=service_idx,
                                    force=force)
    except ValueError as e:  # caller-side errors (missing key / URL)
        # Generic per-app-data failure log so EVERY app (not just the
        # ones with their own module-level logging) is traceable in
        # stdout / Admin -> Logs with the host + chip + module that
        # failed. ValueError = operator-fixable config (missing key /
        # URL) -> WARN (use the `warning:` marker the severity
        # classifier in logic/logs.py keys on).
        slug = getattr(mod, "__name__", "?").rsplit(".", 1)[-1]
        print(f"[apps] warning: app-data config issue host={host_id} "
              f"svc_idx={service_idx} app={slug}: {e}")
        raise HTTPException(400, str(e))
    except RuntimeError as e:  # upstream errors
        # Upstream actually failed (404 / auth / timeout) -> ERROR (the
        # `error:` marker routes it to the ERROR bucket).
        slug = getattr(mod, "__name__", "?").rsplit(".", 1)[-1]
        print(f"[apps] error: app-data fetch failed host={host_id} "
              f"svc_idx={service_idx} app={slug}: {e}")
        raise HTTPException(502, str(e))


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
    try:
        result = await mod.run_skill(skill_id, host_row, chip,
                                     host_id=host_id, service_idx=service_idx)
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


# noinspection PyTypeChecker,PyUnresolvedReferences
@app.get("/api/hosts")
async def api_hosts(force: bool = False):
    """Hosts view — returns the CURATED host list merged with live
    stats from every enabled provider.

    Source of truth is ``hosts_config`` (Settings → Hosts). If it's
    empty, falls back to auto-discovering from the Beszel / Pulse
    batch maps so the view isn't blank for fresh installs.

    NOTE — refactored from the original ~975-line inline
    duplication of the Beszel / Pulse / NE / Webmin probe logic to
    compose the protected helper chain (``_get_host_provider_state`` +
    ``_merge_one_host``) per row. Bearer-token scrapers (Homarr widget,
    Grafana, custom dashboards) hitting THIS endpoint now share the
    SPA's single-flight lock on hub probes AND the per-host
    Webmin success-cache + 5s fail-cache, so a burst of /api/hosts
    calls can no longer recreate the 504-storm pattern. Response shape
    is byte-for-byte identical to the pre-refactor inline version
    (same ``_shape_host_api_row`` per row, same top-level keys,
    same ``provider_errors`` aggregation).

    Each curated host entry specifies its per-provider name:
      - ``ne_url``      — node-exporter scrape URL
      - ``beszel_name`` — Beszel ``host`` field to match
      - ``pulse_name``  — Pulse PVE node name
    For each enabled provider, we fetch once (Beszel + Pulse via the
    cached batch maps; NE + Webmin per-host inside ``_merge_one_host``)
    and merge with the best-of rule — non-zero values win over zeros —
    so flaky providers never erase good data.
    """
    # ---- Provider state (single-flight, cached) -------------------
    # `_get_host_provider_state` does the Beszel + Pulse batch probes
    # once, gated by the lock + cache. On a cache hit it's a dict
    # lookup; on a miss exactly ONE caller pays the probe cost while
    # the rest queue. `force=True` bypasses the TTL but still goes
    # through the lock.
    state = await _get_host_provider_state(force=force)
    active = state["active"]
    beszel_map = state["beszel_map"]
    pulse_map = state["pulse_map"]
    errors: dict[str, str] = dict(state["errors"])

    curated = _load_hosts_config()

    # ---- Fallback: auto-discover from Beszel / Pulse when no curated list ----
    # Same shape the inline path emitted; uses the cached batch maps
    # rather than re-probing.
    if not curated:
        if beszel_map:
            curated = [
                {
                    "id": k,
                    "label": (v or {}).get("beszel_name") or k,
                    "ne_url": "",
                    "beszel_name": k,
                    "pulse_name": "",
                    "enabled": True,
                }
                for k, v in sorted(beszel_map.items(), key=lambda kv: kv[0].lower())
            ]
        elif pulse_map:
            curated = [
                {
                    "id": k,
                    "label": (v or {}).get("pulse_name") or k,
                    "ne_url": "",
                    "beszel_name": "",
                    "pulse_name": k,
                    "enabled": True,
                }
                for k, v in sorted(pulse_map.items(), key=lambda kv: kv[0].lower())
            ]

    # ---- Per-host merge via the protected helper chain ------------
    # Each enabled curated host gets its OWN `_merge_one_host` call.
    # NE + Webmin probes happen per-host inside the helper (Webmin
    # behind the per-host success-cache + 5s fail-cache). Beszel /
    # Pulse hits are dict lookups against the cached batch maps the
    # outer state carries. Run the per-host merges in parallel — same
    # behaviour as the previous inline path's `asyncio.gather` over
    # NE + Webmin probes, just composed via the helper.
    enabled_hosts = [h for h in curated if h.get("enabled", True)]
    if enabled_hosts:
        merge_results = await asyncio.gather(*(
            _merge_one_host(h, state, force=force) for h in enabled_hosts
        ))
    else:
        merge_results = []

    out: list[dict] = []
    for h, (merged, providers_hit) in zip(enabled_hosts, merge_results):
        # If a Webmin probe surfaced an error string in the merged
        # dict (the helper stamps `exporter_error` on full-failure),
        # aggregate it into the top-level provider_errors map so
        # bearer-token clients keep getting the same coarse signal
        # the inline path emitted. First-error-per-provider wins,
        # mirroring the legacy behaviour.
        wm_err = (merged or {}).get("exporter_error")
        if wm_err and "webmin" not in errors and "webmin" in active:
            # Match the legacy "<host_id>: <message>" prefix so
            # downstream dashboards' regex parsers don't break.
            errors["webmin"] = f"{h.get('id')}: {wm_err}"
        out.append({
            "_host_record": h,
            "_merged": merged,
            "_providers": providers_hit,
        })

    # ---- Shape the response ---------------------------------------
    # Snapshot fallback — apply ONCE for every entry whose probes
    # left holes. Loads snapshots in a single DB read, then mutates each
    # entry's merged dict in place, stamping `_stale_fields` /
    # `_stale_ts` on whichever entries had missing fields filled from
    # the snapshot. Same call shape `_merge_one_host` uses for the
    # /api/hosts/one path so both endpoints honour the fallback
    # uniformly.
    try:
        from logic.gather import (
            apply_host_snapshot_fallback as _fallback,
            load_host_snapshots as _load_snaps,
        )
        snaps = _load_snaps()
        if snaps:
            _fallback(
                {entry["_host_record"]["id"]: entry["_merged"] for entry in out},
                snapshots=snaps,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[hosts] snapshot fallback failed: {e}")
    # Short debug spew for core/arch/kernel only — helps diagnose the
    # common "all three columns are empty" complaint by showing each
    # curated host's merged values + which providers contributed.
    hosts = []
    # `docker_node` gating moved to the module-level `_is_swarm_node`
    # helper so `/api/hosts/list` + `/api/hosts/one/{id}` (via
    # `_shape_host_api_row`) and this endpoint share one
    # implementation. No inline set rebuild needed here anymore.
    for entry in out:
        h = entry["_host_record"]
        s = entry["_merged"]
        mounts = s.get("mounts") or []
        nics = s.get("network_ifaces") or []
        print(
            f"[hosts] merged id={h.get('id')!r} "
            f"providers={entry['_providers']} "
            f"cores={s.get('host_cores')!r} "
            f"arch={s.get('host_arch')!r} "
            f"kernel={(s.get('host_kernel') or '')[:40]!r} "
            f"platform={s.get('host_platform')!r} "
            f"os={s.get('host_os')!r} "
            f"mounts={len(mounts)} ({[m.get('n') or m.get('name') for m in mounts]}) "
            f"nics={len(nics)}"
        )
        # Share `_shape_host_api_row` with the new endpoints.
        # Pre-fix the legacy `/api/hosts` built its own inline dict that
        # (a) omitted the `_failure_state_for_host()` spread (sampling_paused
        # + last_failure_ts + consecutive_failures + last_error never
        # reached bearer-token clients), and (b) used a 3-tier status
        # taxonomy that diverged from the canonical six-tier one in
        # `_shape_host_api_row` (paused→down normalisation, `unconfigured`
        # for "no provider mapped", `unknown` only for "providers mapped
        # but no answer"). Scrapers saw false `unknown` → false `down`
        # alerts in Grafana / Apprise. Calling the helper here keeps the
        # legacy endpoint a strict superset of the new ones for any
        # bearer-token client still using it (Homarr widget, scrapers,
        # external automation). Note the helper's `any_provider_enabled`
        # arg — pass True since this endpoint only fires when at least
        # one provider IS active (the early-return at the top of
        # `api_hosts` short-circuits the no-provider case).
        hosts.append(
            _shape_host_api_row(
                h, s, entry["_providers"],
                active=active,
            )
        )

    # Aggregate error — non-fatal; UI shows the first one per provider.
    agg_error = "; ".join(f"{k}: {v}" for k, v in errors.items()) or None

    return {
        "configured": bool(active),
        "active": sorted(active),
        "error": agg_error,
        "provider_errors": errors,
        "hub_url": get_setting(Settings.BESZEL_HUB_URL) or "",
        "hosts": hosts,
        # Counts that let the frontend pick the right empty-state
        # copy — "no curated hosts yet" vs "all curated hosts are
        # disabled" vs "curated hosts exist but no provider matched
        # any of them". Without these the view used to blanket-say
        # "no hosts yet" even when the operator had rows configured.
        "curated_count": len(curated),
        "enabled_count": sum(1 for h in curated if h.get("enabled", True)),
    }


# ---------------------------------------------------------------------------
# Per-host async loading
#
# The monolithic /api/hosts waits until every provider probe for every
# host has returned. With Webmin / Pulse / slow node-exporter scrapes
# this can take 10+ seconds even with the existing parallelisation —
# long enough that the page feels frozen.
#
# The split model:
# GET /api/hosts/list         — skeleton: curated list + global
#                               state (active sources, provider
#                               errors, hub URL). No per-host
#                               probes. Fast (<200ms).
# GET /api/hosts/one/{id}     — single host's merged data. Runs
#                               NE + Webmin probes for THAT host
#                               only; reuses Beszel / Pulse batch
#                               maps from a short-lived cache so a
#                               burst of N parallel calls doesn't
#                               incur N × batch-probe cost.
#
# Legacy /api/hosts still works (metric scrapers / dashboards that
# want one round-trip to see the whole fleet). The SPA calls the
# split pair.
# ---------------------------------------------------------------------------
# cache TTL is now operator-tunable via
# `tuning_host_provider_cache_ttl_seconds`. Default preserved at 10s.
# Resolved at every consumer site (NOT cached at module import) per
# the strict-rule contract.
_host_provider_cache: dict = {"ts": 0.0, "state": None}
# Single-flight guard for ``_get_host_provider_state``. Without
# this, a parallel SPA fan-out of 6 ``/api/hosts/one/<id>`` calls on a
# cold cache fires 6 independent Beszel hub + Pulse probes (each 15-20s),
# saturating the event loop AND the upstream NPM connection pool —
# manifests as 504s on unrelated static-asset requests because they
# queue behind the in-flight probe traffic. With the lock, the first
# caller does the probe; the rest await its result and the cache fills
# from a SINGLE round-trip per provider. Same pattern applies under
# `force=true` (settings-save → forced refresh): the lock prevents 6
# parallel forced calls from each re-running the probe.
_host_provider_lock = asyncio.Lock()

# Lightweight hit/miss diagnostic for the host-provider cache. The
# SPA polls /api/hosts/list every 15s and the TTL defaults to 10s, so
# on a healthy fleet the hit ratio should sit at 90%+. A persistent
# low ratio means the cache key is changing too often (e.g. a settings
# value is bouncing) — operators rarely notice this without a counter.
# Counters reset on container restart AND every log window so
# subsequent entries reflect the recent hit ratio, not the lifetime
# average. The log-emit cadence is operator-tunable via
# `tuning_host_provider_cache_diag_interval` per the strict no-static-
# config rule.
_host_provider_cache_diag: dict = {"hits": 0, "misses": 0}


def _maybe_log_host_provider_cache_diag() -> None:
    """Log a hit/miss summary line every Nth call to the cache. Keeps
    the diagnostic visible in Admin → Logs without spamming on every
    poll tick. Resets the counters after each log so subsequent
    windows reflect the recent hit ratio, not the lifetime average.
    Per-use read of the tunable so an Admin → Config edit takes effect
    on the next window without a restart."""
    total = _host_provider_cache_diag["hits"] + _host_provider_cache_diag["misses"]
    try:
        interval = tuning.tuning_int(Tunable.HOST_PROVIDER_CACHE_DIAG_INTERVAL)
    except (KeyError, ValueError, TypeError):
        interval = 100
    if total < interval:
        return
    hits = _host_provider_cache_diag["hits"]
    misses = _host_provider_cache_diag["misses"]
    pct = (hits * 100) // total if total else 0
    print(f"[provider_state] cache window: {hits} hits / {misses} misses "
          f"({pct}% hit rate over last {total} calls)")
    _host_provider_cache_diag["hits"] = 0
    _host_provider_cache_diag["misses"] = 0


# Per-host Webmin result cache. Webmin probes are the slowest link in
# the /api/hosts/one/{id} path (up to 20s each on slow Miniserv); a
# 30s TTL means repeated drawer opens / refresh ticks within half a
# minute skip the probe entirely and reuse the last known-good stats.
# Cache key is the host_id (one Webmin per host — unlike Beszel/Pulse
# which are multi-tenant). Value is the raw dict returned by
# probe_webmin so _merge_one_host can fold it the same way.
# both Webmin cache TTLs are now operator-tunable via
# `tuning_webmin_host_cache_ttl_seconds` (default 30s, success cache)
# and `tuning_webmin_host_fail_cache_ttl_seconds` (default 5s, negative
# cache). Resolved per consumer-site read.
_webmin_host_cache: dict[str, tuple[float, dict]] = {}
_webmin_host_fail_cache: dict[str, tuple[float, dict]] = {}

# Per-host SNMP result caches — same pattern as the Webmin caches.
# Success cache for 30s, fail cache for 5s. SNMP probes are bounded by
# UDP timeout (default 5s × ~13 OID walks fanned in parallel ≈ 5-8s
# wall-clock on a healthy host) so caching the result for the burst
# fan-out is the same win Webmin gets. Per-host id keying matches the
# Webmin cache; SNMP is per-host, no central hub.
_snmp_host_cache: dict[tuple[Any, frozenset[str] | None], tuple[float, dict]] = {}
_snmp_host_fail_cache: dict[tuple[Any, frozenset[str] | None], tuple[float, dict]] = {}

# Per-host HTTP probe result cache — same pattern as the Webmin / SNMP
# pairs but single-dict with a `had_data` flag in the tuple driving
# which TTL to apply (success vs failure). The cached value is the
# subset of `host_http_*` fields the helper stamped, so a hit reuses
# them without re-running the SELECT. TTLs operator-tunable via
# `tuning_http_probe_host_cache_ttl_seconds` (default 30s) +
# `tuning_http_probe_host_fail_cache_ttl_seconds` (default 5s).
_http_probe_host_cache: dict[str, tuple[float, dict, bool]] = {}


def invalidate_host_provider_cache() -> None:
    """Drop the cached provider state + per-host Webmin results.

    Called from every settings-write path that would change provider
    behaviour: host_stats_source / beszel_* / pulse_* / webmin_* /
    hosts_config. Without this, the SPA's "Save → reload Hosts tab"
    flow keeps showing stale auth_failed states for up to
    ``_HOST_PROVIDER_CACHE_TTL`` seconds (10s) — and stale Webmin
    probe results for up to ``_WEBMIN_HOST_CACHE_TTL`` (30s) — because
    /api/hosts/one/{id} reuses the cached error map. Mirrors the
    invalidation pattern already in place for Portainer / auth /
    OIDC discovery caches.
    """
    _host_provider_cache["ts"] = 0.0
    _host_provider_cache["state"] = None
    _webmin_host_cache.clear()
    _webmin_host_fail_cache.clear()
    # SNMP shares the per-host success / failure cache pattern with
    # Webmin. Bust on every settings-save touching SNMP creds /
    # aliases so the next probe picks up the new community / version /
    # port without waiting out the 30s TTL.
    _snmp_host_cache.clear()
    _snmp_host_fail_cache.clear()
    # HTTP probe per-host cache — same invalidation contract.
    _http_probe_host_cache.clear()


def _compute_host_provider_cache_key() -> tuple[set[str], tuple]:
    """Return (active_sources, cache_key) — the active providers as a
    set + the cache-bust key (sorted-active-tuple + cred-blob-hash).
    Module-level so both ``_get_host_provider_state`` and the cheap
    ``_peek_cached_host_provider_state`` helper share one definition;
    a divergence between the two would mean the peek helper says
    "cache warm" while the get helper recomputes a different key and
    refires the probe. Re-callable so the post-lock path can refresh
    the key after a settings save during the lock-wait without
    risking the queued caller using a pre-save snapshot.
    """
    active_set = active_host_stats_providers()
    # Cache key includes the active-sources tuple so a settings
    # change like flipping `host_stats_source` from "beszel" to
    # "beszel,pulse" auto-busts the cache. Save paths also call
    # `invalidate_host_provider_cache()` directly for instant
    # feedback; the key match is defence-in-depth.
    # Credential-blob hash folded into the key so changing
    # `beszel_password` (without flipping `host_stats_source`)
    # busts the cache too.
    cred_blob = "|".join((
        get_setting(Settings.BESZEL_HUB_URL) or "",
        get_setting(Settings.BESZEL_IDENTITY) or "",
        get_setting(Settings.BESZEL_PASSWORD) or "",
        get_setting(Settings.BESZEL_VERIFY_TLS, "true") or "true",
        get_setting(Settings.PULSE_URL) or "",
        get_setting(Settings.PULSE_TOKEN) or "",
        get_setting(Settings.PULSE_VERIFY_TLS, "true") or "true",
        get_setting(Settings.WEBMIN_URL) or "",
        get_setting(Settings.WEBMIN_USER) or "",
        get_setting(Settings.WEBMIN_PASSWORD) or "",
        get_setting(Settings.WEBMIN_VERIFY_TLS, "true") or "true",
        get_setting(Settings.NODE_EXPORTER_URL_TEMPLATE) or "",
        get_setting(Settings.NODE_EXPORTER_OVERRIDES) or "",
        # SNMP — every credential / default that affects
        # what the probe sees. v3 keys are the security-sensitive
        # ones; the community + port + version + aliases also
        # belong here so a global default change auto-busts the
        # cache without waiting on the explicit invalidate path.
        get_setting(Settings.SNMP_DEFAULT_COMMUNITY) or "",
        get_setting(Settings.SNMP_DEFAULT_VERSION) or "",
        # Default port migrated to TUNABLES — read via tuning_int so a
        # change to `tuning_snmp_default_port` busts this cache too.
        str(tuning.tuning_int(Tunable.SNMP_DEFAULT_PORT)),
        get_setting(Settings.SNMP_V3_USER) or "",
        get_setting(Settings.SNMP_V3_AUTH_KEY) or "",
        get_setting(Settings.SNMP_V3_PRIV_KEY) or "",
        get_setting(Settings.SNMP_ALIASES) or "",
    ))
    cred_hash = hashlib.sha256(cred_blob.encode()).hexdigest()[:16]
    return active_set, (tuple(sorted(active_set)), cred_hash)


def _peek_cached_host_provider_state() -> dict | None:
    """Return the cached host provider state IF warm — else None.

    Cheap, never blocks, never fires a probe. ``api_hosts_list`` uses
    this to decide whether to await ``_get_host_provider_state`` (warm
    case — instant) or serve snapshot rows immediately and kick the
    probe in the background (cold case — Fix A from the cold-load
    analysis). Cache is "warm" iff (a) state object exists, (b) TTL
    not expired, AND (c) the stored cache key still matches the
    current active-providers + cred-hash signature (a settings save
    invalidates the key even before the explicit
    ``invalidate_host_provider_cache`` call lands, so we can't trust a
    stale-key cache to mirror current settings).
    """
    cached = _host_provider_cache.get("state")
    cached_key = _host_provider_cache.get("key")
    if not cached or not cached_key:
        return None
    cache_ttl = tuning.tuning_int(Tunable.HOST_PROVIDER_CACHE_TTL_SECONDS)
    if (time.time() - _host_provider_cache.get("ts", 0.0)) >= cache_ttl:
        return None
    _, current_key = _compute_host_provider_cache_key()
    if cached_key != current_key:
        return None
    return cached


# Single-flight guard for background gather kicks. ``_kick_background_gather``
# fires ``_gather`` as a fire-and-forget task to refresh the items / stacks /
# nodes cache without blocking the response. Without the guard a poll burst
# (auto-refresh every 30s × N tabs open) would fire N concurrent gathers,
# each fanning out to Portainer with the same payload. The guard tracks the
# current task and ignores subsequent kicks while it's still running.
_background_gather_task: "asyncio.Task | None" = None
# Mirror single-flight guard for ``_gather_stats``. Same rationale:
# without it a burst of /api/stats calls would each fire a parallel
# stats gather while the previous one is still running, multiplying
# Portainer + container fan-out cost. The guard tracks the current
# task and ignores subsequent kicks while it's in flight.
_background_stats_task: "asyncio.Task | None" = None


def _kick_background_gather() -> "asyncio.Task | None":
    """Schedule ``_gather`` as a background task if none is running.

    Returns the in-flight task (newly-scheduled OR already-running) so
    a cold-cache caller that genuinely needs fresh data can ``await``
    the same task instead of issuing a parallel gather. Returns
    ``None`` when scheduling failed (no event loop). Callers that
    only need the boolean "is something running?" check
    ``result is not None``.

    Single-flight: if a prior task is still pending the existing task
    is returned unchanged — never spawns two concurrent gathers.
    """
    global _background_gather_task
    try:
        if _background_gather_task is not None and not _background_gather_task.done():
            return _background_gather_task
        loop = asyncio.get_running_loop()
        # `name=` surfaces this task in asyncio debug output / repl
        # debugging so the operator can tell it apart from the dozens
        # of other anonymous create_task sites. Strong-ref pattern
        # (module-level `_background_gather_task`) covers the GC-collection
        # risk per the project's "Background-task lifecycle" rule — the
        # name kwarg adds diagnostic parity without dragging in the full
        # `spawn_background_task` wrapper.
        _background_gather_task = loop.create_task(_gather(), name="apps-kick-background-gather")
        return _background_gather_task
    except RuntimeError:
        # No running event loop (called from a sync context that isn't
        # inside a request handler) — caller can fall back to awaiting
        # ``_gather()`` directly if they really need fresh data.
        return None


def _kick_background_stats_gather() -> bool:
    """Same single-flight pattern as ``_kick_background_gather`` but
    for the stats cache. Used by ``/api/stats`` to serve the warm
    cache instantly + refresh in background. Returns True when a task
    is running (just-scheduled OR already in flight); False on no-loop.

    The seed-from-DB path stamps ``_stats_cache["ts"] = 0.0`` so the
    legacy TTL check at the top of ``api_stats`` would always fall
    through to a synchronous ``_gather_stats()`` and block the response
    on a fresh page load — even though cached values were already
    available to serve. Routing through this kick instead serves the
    seeded cache first, then refreshes in the background; the next
    poll cycle picks up the live values.
    """
    global _background_stats_task
    try:
        if _background_stats_task is not None and not _background_stats_task.done():
            return True
        loop = asyncio.get_running_loop()
        # Same diagnostic-parity rationale as `_kick_background_gather`
        # above — surface the task name in asyncio debug output.
        _background_stats_task = loop.create_task(_gather_stats(), name="apps-kick-background-stats-gather")
        return True
    except RuntimeError:
        return False


# noinspection PyTypeChecker,PyUnresolvedReferences
async def _get_host_provider_state(force: bool = False) -> dict:
    """Fetch + cache the provider state needed to merge any host.

    The "batch" providers (Beszel, Pulse) expose one endpoint that
    returns every host in one call, so we memoise them for
    ``_HOST_PROVIDER_CACHE_TTL`` seconds. A burst of /api/hosts/one/{id}
    calls from the SPA hits the cache; settings changes auto-clear
    after the TTL expires (no explicit invalidation needed).
    """
    now = time.time()
    active, cache_key = _compute_host_provider_cache_key()
    # cache TTL is operator-tunable; resolve once at the top of
    # the function and reuse for both the pre-lock and post-lock checks
    # (within the same call, the value can't legitimately change).
    cache_ttl = tuning.tuning_int(Tunable.HOST_PROVIDER_CACHE_TTL_SECONDS)
    cached = _host_provider_cache.get("state")
    cached_key = _host_provider_cache.get("key")
    if (
        not force and cached and cached_key == cache_key
        and (now - _host_provider_cache.get("ts", 0.0)) < cache_ttl
    ):
        _host_provider_cache_diag["hits"] += 1
        _maybe_log_host_provider_cache_diag()
        return cached
    _host_provider_cache_diag["misses"] += 1
    _maybe_log_host_provider_cache_diag()

    # Single-flight — only ONE concurrent caller does the cold-
    # cache probe; the rest await on the lock and pick up the populated
    # cache via the post-lock re-check below. Pre-fix N parallel
    # /api/hosts/one/<id> calls fired N independent Beszel hub + Pulse
    # probes, saturating the event loop. Force=true requests still
    # serialise here so a SPA settings-save fan-out doesn't 6× the
    # upstream load either.
    # measure the wait so operators can see whether contention
    # is the cause of elevated /api/hosts/one latency vs slow upstreams.
    # First caller bucket-counts in sub-ms (zero wait); subsequent
    # callers in the same fan-out bucket-count in seconds.
    _lock_wait_start = time.monotonic()
    async with _host_provider_lock:
        metrics.HOST_PROVIDER_LOCK_WAIT.observe(time.monotonic() - _lock_wait_start)
        # fix — RE-COMPUTE active + cache_key inside the
        # lock. A settings save during the lock-wait could have changed
        # `host_stats_source` or any credential, so the pre-lock values
        # are stale. Without this re-compute, a queued caller would run
        # a probe under a snapshot that no longer matches the current
        # settings (e.g. probing Beszel after the operator turned it
        # off). Generalisable rule: when single-flighting via lock-then-
        # recheck, re-COMPUTE the cache key inside the lock, don't just
        # re-read the cache.
        active, cache_key = _compute_host_provider_cache_key()
        # Re-check inside the lock: another caller may have populated
        # the cache while we were waiting. ``force`` requests always
        # re-probe but only the FIRST forced caller pays the cost —
        # subsequent forced callers within the same lock-acquire window
        # see a fresh cache (now < TTL) and reuse it.
        now2 = time.time()
        cached2 = _host_provider_cache.get("state")
        cached_key2 = _host_provider_cache.get("key")
        if (
            cached2 and cached_key2 == cache_key
            and (now2 - _host_provider_cache.get("ts", 0.0)) < cache_ttl
        ):
            return cached2

        return await _do_host_provider_probe(active, cache_key)


async def _do_host_provider_probe(active: set[str], cache_key: tuple) -> dict:
    """Inner — runs the Beszel + Pulse probes and writes the result
    cache. Always called under ``_host_provider_lock``. Split from
    the outer function so the lock-acquire path stays narrow.
    """
    from logic import beszel as _beszel
    from logic import pulse as _pulse

    errors: dict[str, str] = {}

    # Beszel + Pulse hub probes run in PARALLEL. Prior sequential
    # version made the cold-cache cost Beszel + Pulse = up to 30s alone,
    # exhausting the 30s `/api/hosts/one/<id>` budget before NE + Webmin
    # even started. With `asyncio.gather`, cold-cache cost drops to
    # max(B, P) ≈ 15s — leaving ~15s for the per-host slice. Both are
    # independent probes hitting different hubs; no shared state, safe
    # to fan out. Each builds its own (config-fetch + probe) coroutine
    # so missing credentials short-circuit cleanly.
    async def _probe_beszel() -> tuple[dict, str | None]:
        if "beszel" not in active:
            return {}, None
        hub_url = get_setting(Settings.BESZEL_HUB_URL) or ""
        ident = get_setting(Settings.BESZEL_IDENTITY) or ""
        passw = get_setting(Settings.BESZEL_PASSWORD) or ""
        verify = (get_setting(Settings.BESZEL_VERIFY_TLS, "true") or "true").lower() == "true"
        if not (hub_url and ident and passw):
            return {}, "missing url / identity / password"
        r = await _beszel.probe_hub(hub_url, ident, passw, verify_tls=verify)
        return r.get("systems") or {}, r.get("error")

    async def _probe_pulse() -> tuple[dict, str | None]:
        if "pulse" not in active:
            return {}, None
        pulse_url = get_setting(Settings.PULSE_URL) or ""
        pulse_token = get_setting(Settings.PULSE_TOKEN) or ""
        verify = (get_setting(Settings.PULSE_VERIFY_TLS, "true") or "true").lower() == "true"
        if not (pulse_url and pulse_token):
            return {}, "missing url / token"
        r = await _pulse.probe_pulse(pulse_url, pulse_token, verify_tls=verify)
        return r.get("hosts") or {}, r.get("error")

    (beszel_map, beszel_err), (pulse_map, pulse_err) = await asyncio.gather(
        _probe_beszel(), _probe_pulse(),
    )
    if beszel_err:
        errors["beszel"] = beszel_err
    if pulse_err:
        errors["pulse"] = pulse_err

    webmin_creds_ok = False
    webmin_user = ""
    webmin_password = ""
    webmin_verify = False
    webmin_aliases: dict[str, str] = {}
    if "webmin" in active:
        webmin_user = get_setting(Settings.WEBMIN_USER) or ""
        webmin_password = get_setting(Settings.WEBMIN_PASSWORD) or ""
        webmin_verify = (get_setting(Settings.WEBMIN_VERIFY_TLS, "false") or "false").lower() == "true"
        try:
            wm_aliases_raw = json.loads(get_setting(Settings.WEBMIN_ALIASES, "{}") or "{}")
            if isinstance(wm_aliases_raw, dict):
                webmin_aliases = {
                    str(k).strip(): str(v).strip()
                    for k, v in wm_aliases_raw.items()
                    if str(k).strip() and str(v).strip()
                }
        except ValueError:
            webmin_aliases = {}
        if webmin_user and webmin_password:
            webmin_creds_ok = True
        else:
            errors["webmin"] = "missing user / password"

    # SNMP — settings-derived defaults flow through state so
    # `_merge_one_host` doesn't re-read them per host. v3 keys are
    # secrets but stay in the in-process state dict (not the wire); the
    # admin-only `/api/snmp/test` endpoint is the only path that lets
    # operators surface them and even there they're write-only via
    # `_set` flags. Per-host overrides on `hosts_config[].snmp` are
    # consulted INSIDE _merge_one_host so a row's own community wins.
    snmp_default_community = ""
    snmp_default_version = "v2c"
    snmp_default_port = 161
    snmp_v3_user = ""
    snmp_v3_auth_key = ""
    snmp_v3_priv_key = ""
    snmp_aliases: dict[str, str] = {}
    if "snmp" in active:
        snmp_default_community = get_setting(Settings.SNMP_DEFAULT_COMMUNITY) or "public"
        snmp_default_version = (
                                   get_setting(Settings.SNMP_DEFAULT_VERSION) or "v2c"
                               ).strip().lower() or "v2c"
        try:
            snmp_default_port = tuning.tuning_int(Tunable.SNMP_DEFAULT_PORT)
        except (TypeError, ValueError):
            snmp_default_port = 161
        snmp_v3_user = get_setting(Settings.SNMP_V3_USER) or ""
        snmp_v3_auth_key = get_setting(Settings.SNMP_V3_AUTH_KEY) or ""
        snmp_v3_priv_key = get_setting(Settings.SNMP_V3_PRIV_KEY) or ""
        try:
            sn_aliases_raw = json.loads(get_setting(Settings.SNMP_ALIASES, "{}") or "{}")
            if isinstance(sn_aliases_raw, dict):
                snmp_aliases = {
                    str(k).strip(): str(v).strip()
                    for k, v in sn_aliases_raw.items()
                    if str(k).strip() and str(v).strip()
                }
        except ValueError:
            snmp_aliases = {}

    state = {
        "active": active,
        "beszel_map": beszel_map,
        "pulse_map": pulse_map,
        "errors": errors,
        "webmin_user": webmin_user,
        "webmin_password": webmin_password,
        "webmin_verify": webmin_verify,
        "webmin_creds_ok": webmin_creds_ok,
        "webmin_aliases": webmin_aliases,
        # SNMP — defaults + aliases. Per-host overrides land
        # later via `hosts_config[].snmp`.
        "snmp_default_community": snmp_default_community,
        "snmp_default_version": snmp_default_version,
        "snmp_default_port": snmp_default_port,
        "snmp_v3_user": snmp_v3_user,
        "snmp_v3_auth_key": snmp_v3_auth_key,
        "snmp_v3_priv_key": snmp_v3_priv_key,
        "snmp_aliases": snmp_aliases,
    }
    _host_provider_cache["ts"] = time.time()
    _host_provider_cache["state"] = state
    _host_provider_cache["key"] = cache_key
    return state


def _publish_provider_probe_event(host_id: str, provider: str, kind: str,
                                  started_at: float | None = None,
                                  *, client_id: str | None = None,
                                  ok: bool | None = None) -> None:
    """Fire a per-(provider, host) probe-status SSE event.

    ``kind`` is either ``probing`` (slice entered, real fetch about to
    run) or ``done`` (slice complete — success OR failure). The SPA's
    ``host:provider_probing`` / ``host:provider_done`` handlers track
    ``h._polling[provider] = bool`` per row so the chip pulses ONLY
    while ITS probe is in flight (not the row-wide `_loading` window).

    Cache-hit paths skip these events — no actual fetch happened, the
    chip shouldn't pulse for a microsecond dict lookup. Slow probes
    (SNMP walks, Webmin three-tier fallback) are the operators' real
    interest.

    ``client_id`` threads the originating tab's UUID into the event so
    the SPA's `_isSelfEvent` self-filter works on this event the same
    way it does on every other write-handler-published event. Without
    it the originating tab still receives + processes its own events
    (cost is microscopic, but inconsistent with the rest of the
    publish surface). Caller passes `_request_client_id(request)` from
    the request-scoped header.

    Errors are logged + swallowed; a failed publish must never break
    the probe path.
    """
    try:
        payload: dict[str, Any] = {
            "host_id": host_id,
            "provider": provider,
        }
        if kind == "probing":
            payload["started_at"] = started_at if started_at is not None else time.time()
        elif kind == "done":
            payload["finished_at"] = time.time()
            if started_at is not None:
                payload["duration_ms"] = int((time.time() - started_at) * 1000)
            # Outcome hint — lets the SPA's `host:provider_done`
            # handler flip the chip to its known-good (ok=True) /
            # known-failed (ok=False) state from the SSE event itself,
            # without waiting for the next /api/hosts/one/{id} round-
            # trip. Snappier on slow networks. Caller passes ok=True
            # on success branch, ok=False on failure branch, leaves
            # None when the outcome isn't yet decided (e.g. cache-hit
            # paths skip the events entirely so this is rare).
            if ok is not None:
                payload["ok"] = bool(ok)
        _events.publish(f"host:provider_{kind}", payload, client_id=client_id)
    except Exception as e:  # noqa: BLE001
        print(f"[hosts] provider_{kind} publish failed for "
              f"{host_id!r}/{provider!r}: {e}")


def bulk_detected_ports(host_ids: list) -> dict:
    """bulk version of :func:`_populate_detected_ports` for many hosts.

    Returns ``{host_id: (detected_ports_list, last_port_scan_ts)}``. Hosts with
    no scan rows are absent from the dict (caller leaves the merged dict
    untouched, matching the single-host helper's behaviour). Two SQL queries
    total — one for the latest (scan_id, ts) per host, one for the ports
    belonging to those scan_ids — INDEPENDENT of fleet size; callers in the
    ``/api/hosts/list`` loop replace the per-host helper with a dict lookup.

    Capped by SQLite's parameter limit (999 by default); chunk if a fleet
    ever pushes past ~500 hosts.
    """
    ids = [hid for hid in (host_ids or []) if hid]
    if not ids:
        return {}
    out: dict = {}
    try:
        with db_conn() as c:
            placeholders = ",".join(["?"] * len(ids))
            # Latest scan per host: rows where ts = MAX(ts) per host_id. If
            # two scans share the max ts (rare), pick the lexicographically
            # smallest scan_id for determinism (matches the single-host
            # helper's "ORDER BY ts DESC LIMIT 1" + the index covering shape).
            head_rows = c.execute(
                f"SELECT s.host_id, s.scan_id, s.ts FROM host_port_scans s "
                f"INNER JOIN (SELECT host_id, MAX(ts) AS mts FROM host_port_scans "
                f"            WHERE host_id IN ({placeholders}) GROUP BY host_id) m "
                f"ON s.host_id = m.host_id AND s.ts = m.mts",
                ids,
            ).fetchall()
            # Typed as `dict[str, tuple[Any, int]]` so the static
            # analyser narrows `cur` to a concrete tuple after the None
            # branch — pre-fix the dict was annotated bare `dict` so
            # `cur[0]` was flagged as a subscript on `Any | None` even
            # after the explicit None check below.
            per_host_head: dict[str, tuple[Any, int]] = {}
            for r in head_rows:
                hid = r["host_id"]
                sid = r["scan_id"]
                if not hid or not sid:
                    continue
                ts = int(r["ts"] or 0)
                cur = per_host_head.get(hid)
                # Explicit None-then-compare instead of `cur is None or
                # sid < cur[0]` — the two-branch form makes the
                # narrowing explicit so the IDE recognises that
                # `cur[0]` is only subscripted on a concrete tuple.
                if cur is None:
                    per_host_head[hid] = (sid, ts)
                elif sid < cur[0]:
                    per_host_head[hid] = (sid, ts)
            if not per_host_head:
                return {}
            scan_ids = list({sid for (sid, _ts) in per_host_head.values()})
            sph = ",".join(["?"] * len(scan_ids))
            port_rows = c.execute(
                f"SELECT scan_id, port, service_hint, banner_excerpt, protocol "
                f"FROM host_port_scans WHERE scan_id IN ({sph}) "
                f"ORDER BY protocol ASC, port ASC",
                scan_ids,
            ).fetchall()
            ports_by_scan: dict = {}
            for r in port_rows:
                ports_by_scan.setdefault(r["scan_id"], []).append(r)
            for hid, (sid, ts) in per_host_head.items():
                rows = ports_by_scan.get(sid, [])
                out[hid] = ([{
                    "port": int(r["port"]),
                    "protocol": (r["protocol"] or "tcp"),
                    "service_hint": r["service_hint"] or "",
                    "banner_excerpt": r["banner_excerpt"] or "",
                    "scanned_at": ts,
                } for r in rows], ts)
    except Exception as e:  # noqa: BLE001
        print(f"[port_scan] bulk_detected_ports failed: {e}")
        return {}
    return out


def _populate_detected_ports(host_id: str, merged: dict) -> bool:
    """Fill `merged.detected_ports[]` + `merged.last_port_scan_ts` from
    the latest scan in `host_port_scans` for this host. Returns True
    when at least one row was found (caller bumps `providers_hit`).

    Reads UNCONDITIONALLY — port-scan history visibility is independent
    of (a) the master `port_scan_enabled` toggle, (b) the per-host
    `port_scan.enabled` flag, AND (c) the live-provider reachability
    state of the host's other providers (Beszel / NE / Pulse / Webmin
    / SNMP). Operator-flagged: "what was open last time we scanned"
    must remain viewable on hosts where the providers are unreachable
    AND on hosts where port-scanning has been disabled. Disabling the
    toggle should stop NEW scans, not erase the historical view.

    Used by both `_merge_one_host` (the live-provider path) and
    `api_hosts_list` (the snapshot-only skeleton path) so detected
    ports surface uniformly regardless of which endpoint the SPA
    is hitting.
    """
    if not host_id:
        return False
    try:
        with db_conn() as c:
            # The newest row's scan_id IS the newest scan — a plain
            # ORDER BY ts DESC LIMIT 1 is covered by the (host_id, ts DESC)
            # index and avoids a full GROUP BY + MAX aggregate. Called
            # per-row on both hosts endpoints, so keep it index-friendly.
            head = c.execute(
                "SELECT scan_id, ts FROM host_port_scans "
                "WHERE host_id = ? ORDER BY ts DESC LIMIT 1",
                (host_id,),
            ).fetchone()
            if not head or not head["scan_id"]:
                return False
            rows = c.execute(
                "SELECT port, service_hint, banner_excerpt, protocol "
                "FROM host_port_scans WHERE scan_id = ? "
                "ORDER BY protocol ASC, port ASC",
                (head["scan_id"],),
            ).fetchall()
            merged["detected_ports"] = [{
                "port": int(r["port"]),
                "protocol": (r["protocol"] or "tcp"),
                "service_hint": r["service_hint"] or "",
                "banner_excerpt": r["banner_excerpt"] or "",
                "scanned_at": int(head["ts"] or 0),
            } for r in rows]
            merged["last_port_scan_ts"] = int(head["ts"] or 0)
            return True
    except Exception as e:  # noqa: BLE001
        print(f"[port_scan] read failed for {host_id!r}: {e}")
        return False


# noinspection PyUnresolvedReferences,PyTypeChecker
async def _process_hub_provider(
    *,
    name: str,
    h: dict,
    key: str,
    hub_map: dict,
    lookup_fn,
    hub_errors: dict,
    status_field: str,
    pause_round_threshold: int,
    merged: dict,
    providers_hit: list[str],
    active,
) -> None:
    """Canonical state machine for a HUB-style provider (Pulse, Beszel).

    Shared shape across the two providers + any future hub-style
    addition (Glances, PocketBase-style, etc.):

      1. gate on `name in active` + `key` non-empty (per-host opt-in)
      2. gate on `_is_provider_paused(h["id"], name)` short-circuit
      3. lookup `key` in `hub_map` via `lookup_fn`
      4. on hit:
         - if `<status_field>` is down/paused/unreachable AND hub_ok:
           record failure
         - else: merge stats into `merged`, append to `providers_hit`,
           record success
      5. on miss + hub_ok: record "host not found in hub map" failure

    `hub_ok` is computed from `hub_errors` (state["errors"] — when
    `name not in hub_errors` the hub fetch succeeded so we can blame
    a per-host miss on the host itself; otherwise we suppress the
    failure record to avoid cascade-pausing every host on a hub blip).

    Mutates `merged` + `providers_hit` in place. Awaits the
    per-(provider, host) outcome recorder.

    `lookup_fn` is provider-specific — Pulse uses `_pulse.lookup`
    (case + whitespace tolerant); Beszel passes `lambda m, k: m.get(k)`
    (exact-match dict get). The status-field name varies per provider
    (`pulse_status` / `beszel_status`) — extract once at the call site.
    """
    if name not in active or not key:
        return
    if _is_provider_paused(h["id"], name):
        return
    stats = lookup_fn(hub_map, key)
    hub_ok = name not in hub_errors
    if stats:
        st = (stats.get(status_field) or "").lower()
        if st in ("down", "paused", "unreachable"):
            if hub_ok:
                await _record_provider_outcome(
                    h["id"], name, False,
                    error=f"{name} status={st}",
                    round_threshold=pause_round_threshold,
                )
        else:
            _merge_best(merged, stats)
            providers_hit.append(name)
            await _record_provider_outcome(h["id"], name, True)
    elif hub_ok:
        await _record_provider_outcome(
            h["id"], name, False,
            error=f"host not found in {name.capitalize()} hub map",
            round_threshold=pause_round_threshold,
        )


# noinspection PyTypeChecker,PyUnresolvedReferences
async def _merge_one_host(h: dict, state: dict, *, force: bool = False,
                          client_id: str | None = None) -> tuple[dict, list[str]]:
    """Merge one curated host with provider data. Runs NE + Webmin
    probes inline for THIS host only; Beszel/Pulse lookups hit the
    cached batch maps. Returns (merged_dict, providers_hit).

    when ``force=True``, drop this host's per-host Webmin
    caches (success + failure) before the probe block so the next
    `probe_webmin` call hits the wire. Pre-fix `?force=true` only
    bypassed the OUTER `_host_provider_cache`; the 30s success cache
    + 5s failure cache still served the previously-cached entry.
    Operators expect "force = re-probe everything for THIS host".
    Settings-save paths already invalidate every cache via
    `invalidate_host_provider_cache()`; this is the per-host force-
    refresh path (drawer reopen with `?force=true`).

    Per-provider polling SSE events: each per-host probe slice that
    actually hits the wire (cache miss) is bracketed by
    ``host:provider_probing`` / ``host:provider_done`` events keyed
    on (host_id, provider). The SPA's chip pulse driver consumes them
    so a chip pulses ONLY while ITS probe is in flight, not the
    whole row-wide `_loading` window. Cache hits skip the events
    (no real fetch happened) so the chip stays at rest.
    """
    from logic import node_exporter as _ne
    from logic import pulse as _pulse
    from logic import webmin as _webmin

    merged: dict = {}
    providers_hit: list[str] = []
    active = state["active"]
    if force:
        _webmin_host_cache.pop(h["id"], None)
        _webmin_host_fail_cache.pop(h["id"], None)
        # SNMP per-host caches — same force=true contract as
        # Webmin. Drop both success + fail entries so the next probe
        # block hits the wire and produces a fresh sample.
        _snmp_host_cache.pop(h["id"], None)
        _snmp_host_fail_cache.pop(h["id"], None)

    # Pulse — coarse fallback layer.
    # HARD-GATE on explicit `pulse_name`. Pre-fix the lookup fell through to `h["id"]` when no
    # alias was set, so every host got probed against the Pulse hub
    # using its host_id; the lookup always missed for non-Pulse hosts
    # and the "host not found in Pulse hub map" failure incremented
    # consecutive_failures until auto-pause. Operators saw "Pulse
    # paused" on hosts they'd never configured for Pulse. Strict gate:
    # operator must set `pulse_name` explicitly to opt this host into
    # the Pulse probe.
    await _process_hub_provider(
        name="pulse",
        h=h,
        key=(h.get("pulse_name") or "").strip(),
        hub_map=state["pulse_map"],
        lookup_fn=_pulse.lookup,
        hub_errors=state.get("errors") or {},
        status_field="pulse_status",
        pause_round_threshold=tuning.tuning_int(Tunable.PULSE_FAILURE_PAUSE_ROUNDS),
        merged=merged,
        providers_hit=providers_hit,
        active=active,
    )

    # SNMP — runs AFTER Pulse but BEFORE Beszel so the unix-
    # style providers can override SNMP's coarser data wherever they
    # have visibility. Each curated row can override community / port
    # / version / v3 keys via `hosts_config[].snmp`; falls through to
    # the global defaults from state otherwise. Per-host alias map
    # (Docker hostname → SNMP target) wins over the row's snmp_name.
    # Per-host enable gate : the row's `snmp.enabled` is an
    # explicit OPT-IN, parallel to ping.enabled. Default-OFF when the
    # flag is missing — the operator must check the per-host SNMP
    # enable box for the probe to fire, even when snmp_name is set.
    if "snmp" in active:
        from logic import snmp as _snmp
        _raw_row_snmp = h.get("snmp")
        row_snmp: dict = _raw_row_snmp if isinstance(_raw_row_snmp, dict) else {}
        snmp_enabled = row_snmp.get("enabled") is True
        # HARD-GATE: probe ONLY when an alias OR a curated `snmp_name`
        # resolves a target. The previous bare-`h["id"]` fallthrough fanned
        # out probes to every host on fleet-enable, ~all-but-mapped of which
        # timed out. Resolution chain: alias > snmp_name > SKIP.
        # Resolution chain for the SNMP probe target:
        #   1. snmp_aliases[h["id"]]   — global Docker-hostname → SNMP-target map
        #   2. h["snmp_name"]          — per-host SNMP-specific target override
        #   3. h["address"]            — curated dedicated probe target (used by
        #                                 port-scan + ping + SSH too — single
        #                                 source of truth for "the LAN address
        #                                 of this host" so disabling other
        #                                 providers doesn't leave SNMP without
        #                                 a target)
        #   4. ""                      — SKIP (no target → no probe)
        snmp_target = (
            (state.get("snmp_aliases") or {}).get(h["id"])
            or (h.get("snmp_name") or "").strip()
            or (h.get("address") or "").strip()
            or ""
        )
        # Per-(snmp, host) auto-pause short-circuit. When the
        # operator-set threshold has been hit on the sampler path the
        # probe is SKIPPED entirely — no cool-down arming, no log spam,
        # no token spend. Operator clears via POST
        # /api/hosts/{id}/provider/snmp/resume; until then the SPA
        # renders the SNMP chip in its Paused state via
        # `provider_pause_state.snmp.paused`.
        snmp_paused = _is_provider_paused(h["id"], "snmp")
        if snmp_target and snmp_enabled and not snmp_paused:
            now = time.time()
            # SNMP per-host caches use SNMP-specific TTLs (was reusing
            # the Webmin pair; operator changing Webmin TTL silently changed
            # SNMP cache behaviour).
            snmp_success_ttl = tuning.tuning_int(Tunable.SNMP_HOST_CACHE_TTL_SECONDS)
            snmp_fail_ttl = tuning.tuning_int(Tunable.SNMP_HOST_FAIL_CACHE_TTL_SECONDS)
            # Resolve the per-host vendor override BEFORE the cache
            # lookup so the cache key includes it. Without that, an
            # operator changing `row.snmp.vendors` from `["dell"]` to
            # `["dell", "cisco"]` keeps serving the cached `["dell"]`
            # result for `tuning_snmp_host_cache_ttl_seconds` (default
            # 30s) so the new Cisco walks don't kick in until expiry.
            # Including the frozenset in the key auto-invalidates on
            # edit. None vendors (auto-detect) hash distinctly.
            snmp_vendors = _clean_vendors_input(row_snmp.get("vendors"))
            cache_key = (
                h["id"],
                frozenset(snmp_vendors) if snmp_vendors else None,
            )
            cached = _snmp_host_cache.get(cache_key)
            if cached and (now - cached[0]) < snmp_success_ttl:
                result = cached[1]
            else:
                fail_cached = _snmp_host_fail_cache.get(cache_key)
                if fail_cached and (now - fail_cached[0]) < snmp_fail_ttl:
                    result = fail_cached[1]
                else:
                    community = (row_snmp.get("community") or "").strip() \
                                or state.get("snmp_default_community") or "public"
                    version = ((row_snmp.get("version") or "").strip().lower()
                               or state.get("snmp_default_version") or "v2c")
                    try:
                        port = int(row_snmp.get("port")
                                   or state.get("snmp_default_port") or 161)
                    except (TypeError, ValueError):
                        port = 161
                    v3_user = ((row_snmp.get("v3_user") or "").strip()
                               or state.get("snmp_v3_user") or "")
                    v3_auth = ((row_snmp.get("v3_auth_key") or "").strip()
                               or state.get("snmp_v3_auth_key") or "")
                    v3_priv = ((row_snmp.get("v3_priv_key") or "").strip()
                               or state.get("snmp_v3_priv_key") or "")
                    # consume tuning_snmp_probe_timeout_seconds.
                    snmp_timeout = float(tuning.tuning_int(Tunable.SNMP_PROBE_TIMEOUT_SECONDS))
                    # Per-host walk_concurrency override — let server-
                    # class BMCs (Dell iDRAC, Cisco IMC, Supermicro IPMI)
                    # opt out of the safety-floor concurrency=1 default
                    # without affecting flaky low-end agents on the
                    # same fleet.
                    snmp_walk_conc = row_snmp.get("walk_concurrency")
                    try:
                        snmp_walk_conc = int(snmp_walk_conc) if snmp_walk_conc else None
                    except (TypeError, ValueError):
                        snmp_walk_conc = None
                    # Per-host wall-clock budget override. Same
                    # contract as walk_concurrency — None = use the
                    # global tunable; explicit int = override.
                    snmp_wcb = row_snmp.get("wall_clock_budget")
                    try:
                        snmp_wcb = float(snmp_wcb) if snmp_wcb else None
                    except (TypeError, ValueError):
                        snmp_wcb = None
                    # Per-host mount-exclusion list. SNMP agents can
                    # mis-classify pseudo-filesystems as fixed disks
                    # (dd-wrt's `/opt` shows up as a 232 GB
                    # hrStorageFixedDisk on a 16 MB router); the
                    # operator opts those paths out by listing them
                    # here. `_DEFAULT_EXCLUDE_MOUNT_PREFIXES` in
                    # logic/snmp.py covers the universal pseudo-fs
                    # paths automatically; this list adds anything
                    # device-specific.
                    snmp_excludes = row_snmp.get("exclude_mounts") or []
                    if not isinstance(snmp_excludes, list):
                        snmp_excludes = []
                    # Per-provider probing event — fires only on cache
                    # MISS (we're inside the cache-miss branch). Cache
                    # hits skip the event entirely so the chip stays at
                    # rest for the microsecond dict lookup.
                    _probe_started = time.time()
                    _publish_provider_probe_event(h["id"], "snmp", "probing", _probe_started, client_id=client_id)
                    # Pre-init so the finally block's `result.get(...)`
                    # is safe even if the await raises a BaseException
                    # (KeyboardInterrupt / asyncio.CancelledError) that
                    # the broad `except Exception` doesn't catch.
                    result: dict = {"hosts": {}}
                    try:
                        result = await _snmp.probe_snmp(
                            snmp_target,
                            community=community,
                            version=version,
                            port=port,
                            v3_user=v3_user,
                            v3_auth_key=v3_auth,
                            v3_priv_key=v3_priv,
                            active_sources=active,
                            timeout=snmp_timeout,
                            walk_concurrency=snmp_walk_conc,
                            vendors=snmp_vendors,
                            wall_clock_budget=snmp_wcb,
                            exclude_mounts=snmp_excludes,
                        )
                    except Exception as e:  # noqa: BLE001
                        result = {"hosts": {}, "error": f"snmp probe failed: {e}"}
                    finally:
                        _publish_provider_probe_event(
                            h["id"], "snmp", "done", _probe_started,
                            client_id=client_id,
                            ok=bool(result.get("hosts") or {}),
                        )
                    if result.get("hosts") or {}:
                        _snmp_host_cache[cache_key] = (now, result)
                        _snmp_host_fail_cache.pop(cache_key, None)
                        # Per-(snmp, host) success path. Routes through
                        # `record_provider_outcome` so the
                        # `host_provider_last_ok` UPSERT lands — the
                        # chip's "Updated Xm ago" subtitle reads from
                        # that table. Mirrors the Webmin sister block.
                        try:
                            await _record_provider_outcome(h["id"], "snmp", True)
                        except Exception as ex:
                            print(f"[hosts] snmp success-record "
                                  f"failed for {h.get('id')!r}: {ex}")
                    else:
                        _snmp_host_fail_cache[cache_key] = (now, result)
                        _snmp_host_cache.pop(cache_key, None)
                        err = result.get("error") or "empty hosts map"
                        err_str = str(err)
                        # Cool-down responses are SKIPS, not real
                        # failures. Pre-fix the log line read
                        # "[hosts] snmp probe failed for 'idrac': ..."
                        # which the persistent-log severity classifier
                        # in `logic/logs.py:_severity_for` matched on
                        # the word "failed" → painted as ERROR in
                        # Admin → Logs. Cool-down on every drawer poll
                        # then floods the ERROR bucket with red lines
                        # despite nothing actually going wrong. Branch
                        # the log: cool-down skips use the verb
                        # "skipped" (no "fail/error" keywords → INFO
                        # severity); real failures keep "failed" →
                        # ERROR. Both include the resolved SNMP target
                        # alongside the host id so operators tracing
                        # back-off can see what hostname / IP was
                        # being probed without knowing the host_id →
                        # snmp_name mapping by heart.
                        skipped = (
                            result.get("skipped_cooldown")
                            or ("cool-down" in err_str)
                        )
                        target_str = snmp_target or h.get("id") or "?"
                        if skipped:
                            print(
                                f"[hosts] snmp probe skipped (cool-down) "
                                f"for {h.get('id')!r} → {target_str}: {err}"
                            )
                        else:
                            print(
                                f"[hosts] snmp probe failed "
                                f"for {h.get('id')!r} → {target_str}: {err}"
                            )
                        # Per-(snmp, host) auto-pause counter — gated
                        # on `not skipped` so cool-down skips don't
                        # count toward the round threshold (the probe
                        # wasn't actually attempted). Mirrors the
                        # Webmin sister block. Real failures (timeout,
                        # auth, no response) DO count.
                        if not skipped:
                            try:
                                _snmp_threshold = tuning.tuning_int(
                                    Tunable.SNMP_FAILURE_PAUSE_ROUNDS
                                )
                                await _record_provider_outcome(
                                    h["id"], "snmp", False,
                                    error=err_str,
                                    round_threshold=_snmp_threshold,
                                )
                            except Exception as ex:
                                print(f"[hosts] snmp failure-record "
                                      f"failed for {h.get('id')!r}: {ex}")
            hosts_map = result.get("hosts") or {}
            if hosts_map:
                stats = next(iter(hosts_map.values()))
                _merge_best(merged, stats)
                providers_hit.append("snmp")
                # Capture the auto-detected vendor set from the probe
                # diagnostic so the Admin → Hosts editor can render
                # "Auto-detect last result: <vendors>" below the
                # vendor checkbox group. Helps operators new to SNMP
                # decide between trusting auto-detect vs setting an
                # explicit override.
                av = result.get("active_vendors")
                if isinstance(av, list) and av:
                    merged["host_snmp_active_vendors"] = list(av)
                avs = result.get("active_vendors_source")
                if isinstance(avs, str) and avs:
                    merged["host_snmp_active_vendors_source"] = avs

    # Beszel.
    # HARD-GATE on explicit `beszel_name`. Pre-fix the lookup fell through to `h["id"]` when no
    # alias was set, so non-Beszel hosts accumulated "host not found
    # in Beszel hub map" failures and auto-paused on a provider they
    # were never configured for.
    await _process_hub_provider(
        name="beszel",
        h=h,
        key=(h.get("beszel_name") or "").strip(),
        hub_map=state["beszel_map"],
        # Beszel uses bare dict.get (no forgiving alias matching) — match
        # the pre-helper behaviour. Pulse passes _pulse.lookup, which
        # handles case + whitespace tolerance.
        lookup_fn=lambda m, k: m.get(k),
        hub_errors=state.get("errors") or {},
        status_field="beszel_status",
        pause_round_threshold=tuning.tuning_int(Tunable.BESZEL_FAILURE_PAUSE_ROUNDS),
        merged=merged,
        providers_hit=providers_hit,
        active=active,
    )

    # Node-exporter (per-host probe).
    # operator-tunable timeout via `tuning_node_exporter_probe_timeout_seconds`.
    if "node_exporter" in active and h.get("ne_url"):
        # Per-(node_exporter, host) auto-pause short-circuit.
        if not _is_provider_paused(h["id"], "node_exporter"):
            _ne_timeout = tuning.tuning_int(Tunable.NODE_EXPORTER_PROBE_TIMEOUT_SECONDS)
            _ne_pause_rounds = tuning.tuning_int(Tunable.NODE_EXPORTER_FAILURE_PAUSE_ROUNDS)
            # Per-provider probing SSE event — NE has no per-host
            # cache (each call hits the wire), so every entry to this
            # block fires the start/done pair.
            _probe_started = time.time()
            _publish_provider_probe_event(h["id"], "node_exporter", "probing", _probe_started, client_id=client_id)
            # Track outcome so the `done` event carries the ok hint.
            ne_ok = False
            try:
                async with httpx.AsyncClient(verify=False, timeout=float(_ne_timeout)) as ne_client:
                    stats = await _ne.probe_node(ne_client, h["ne_url"])
                _merge_best(merged, stats or {})
                if stats and not stats.get("exporter_error"):
                    providers_hit.append("node_exporter")
                    await _record_provider_outcome(h["id"], "node_exporter", True)
                    ne_ok = True
                else:
                    err = (stats or {}).get("exporter_error") or "no response"
                    await _record_provider_outcome(
                        h["id"], "node_exporter", False,
                        error=str(err),
                        round_threshold=_ne_pause_rounds,
                    )
            except Exception as e:  # noqa: BLE001
                print(f"[hosts] NE probe failed for {h.get('id')!r}: {e}")
                await _record_provider_outcome(
                    h["id"], "node_exporter", False,
                    error=str(e),
                    round_threshold=_ne_pause_rounds,
                )
            finally:
                _publish_provider_probe_event(
                    h["id"], "node_exporter", "done", _probe_started,
                    client_id=client_id, ok=ne_ok,
                )

    # Webmin (per-host probe, 20s outer budget matching api_hosts).
    # Consults a 30s per-host result cache — Webmin is the slowest
    # provider, so burst-refreshes (e.g. the SPA fanning out
    # /api/hosts/one/{id} twice in a minute) skip the repeat probe.
    if "webmin" in active and state["webmin_creds_ok"]:
        wm_url = state["webmin_aliases"].get(h["id"]) or h.get("webmin_url") or ""
        # Per-(webmin, host) auto-pause short-circuit. Same
        # contract as the SNMP block above — operator clears via POST
        # /api/hosts/{id}/provider/webmin/resume.
        webmin_paused = _is_provider_paused(h["id"], "webmin")
        if wm_url and webmin_paused:
            wm_url = ""  # signal "skip" without re-indenting the rest
        if wm_url:
            now = time.time()
            # both cache TTLs are operator-tunable. Resolved
            # once per call (the same TTLs apply across both branches
            # of the if/else below).
            wm_success_ttl = tuning.tuning_int(Tunable.WEBMIN_HOST_CACHE_TTL_SECONDS)
            wm_fail_ttl = tuning.tuning_int(Tunable.WEBMIN_HOST_FAIL_CACHE_TTL_SECONDS)
            cached = _webmin_host_cache.get(h["id"])
            if cached and (now - cached[0]) < wm_success_ttl:
                result = cached[1]
            else:
                # Negative-result cache — short-circuit a recently-
                # failed probe so a SPA fan-out burst doesn't burn 20s ×
                # PARALLEL on an unreachable Webmin. Tunable TTL means
                # recovery is felt within one Hosts-tab refresh cycle
                # at the default 5s.
                fail_cached = _webmin_host_fail_cache.get(h["id"])
                if fail_cached and (now - fail_cached[0]) < wm_fail_ttl:
                    result = fail_cached[1]
                else:
                    # Webmin probe budget is operator-tunable;
                    # shared with the legacy `api_hosts` consumer.
                    _wm_budget = tuning.tuning_int(Tunable.WEBMIN_PROBE_BUDGET_SECONDS)
                    # Per-provider probing SSE event (cache miss only).
                    _probe_started = time.time()
                    _publish_provider_probe_event(h["id"], "webmin", "probing", _probe_started, client_id=client_id)
                    # Pre-init for the finally's `result.get(...)` so a
                    # BaseException (CancelledError / KeyboardInterrupt)
                    # doesn't crash the SSE publish.
                    result: dict = {"hosts": {}}
                    try:
                        result = await asyncio.wait_for(
                            _webmin.probe_webmin(
                                wm_url, state["webmin_user"], state["webmin_password"],
                                verify_tls=state["webmin_verify"],
                                active_sources=active,
                            ),
                            timeout=_wm_budget,
                        )
                    except asyncio.TimeoutError:
                        result = {"hosts": {}, "error": f"webmin probe timeout after {_wm_budget}s"}
                    except Exception as e:  # noqa: BLE001
                        result = {"hosts": {}, "error": f"webmin probe failed: {e}"}
                    finally:
                        _publish_provider_probe_event(
                            h["id"], "webmin", "done", _probe_started,
                            client_id=client_id,
                            ok=bool(result.get("hosts") or {}),
                        )
                    # Cache the OUTCOME — successes go in the long-lived
                    # cache (30s TTL), failures go in the negative cache
                    # (5s TTL) so a hung Webmin doesn't re-burn 20s on
                    # every parallel call. Recovery is felt within 5s
                    # because the fail cache is short.
                    if result.get("hosts") or {}:
                        _webmin_host_cache[h["id"]] = (now, result)
                        _webmin_host_fail_cache.pop(h["id"], None)
                        # Per-(webmin, host) success path. Routes through
                        # `record_provider_outcome` (NOT bare _clear_failure)
                        # so the `host_provider_last_ok` UPSERT lands —
                        # the chip's "Updated Xm ago" subtitle reads from
                        # that table. Pre-fix the bypass left the subtitle
                        # invisible forever for Webmin chips.
                        try:
                            await _record_provider_outcome(h["id"], "webmin", True)
                        except Exception as ex:
                            print(f"[hosts] webmin success-record "
                                  f"failed for {h.get('id')!r}: {ex}")
                    else:
                        _webmin_host_fail_cache[h["id"]] = (now, result)
                        # Drop any stale success entry so the negative-cache's
                        # "fast failure detection" claim actually holds. Pre-fix
                        # a host whose success cache was populated 25s ago + has
                        # just gone down would keep serving the stale success
                        # for 5 more seconds (until the success cache's 30s TTL
                        # expired) because the success cache lookup
                        # short-circuits before the fail cache is even
                        # consulted.
                        _webmin_host_cache.pop(h["id"], None)
                        err = result.get("error") or "empty hosts map"
                        # Same severity / target-clarity branch as the
                        # SNMP block. Cool-down skips use the
                        # verb "skipped" so the persistent-log
                        # severity classifier doesn't flag them as
                        # ERROR; real failures keep "failed". Both
                        # include the resolved Webmin URL alongside
                        # the host id so operators tracing back-off
                        # can see WHAT was being probed.
                        err_str = str(err)
                        skipped = result.get("skipped_cooldown") or ("cool-down" in err_str)
                        wm_target = wm_url or h.get("id") or "?"
                        if skipped:
                            print(f"[hosts] webmin probe skipped (cool-down) "
                                  f"for {h.get('id')!r} → {wm_target}: {err}")
                        else:
                            print(f"[hosts] webmin probe failed "
                                  f"for {h.get('id')!r} → {wm_target}: {err}")
                        # Per-(webmin, host) auto-pause counter.
                        # Cool-down responses are SKIPPED (probe wasn't
                        # actually attempted) so they don't count toward
                        # the round threshold. Structured-skip detection:
                        # prefer `result.get("skipped_cooldown")` when the
                        # probe wires it; fall back to substring match for
                        # legacy. Real failures (HTTP 5xx, timeout,
                        # connection refused, agent rejection) DO count.
                        if not skipped:
                            try:
                                _wm_threshold = tuning.tuning_int(
                                    Tunable.WEBMIN_FAILURE_PAUSE_ROUNDS
                                )
                                await _record_provider_outcome(
                                    h["id"], "webmin", False,
                                    error=err_str,
                                    round_threshold=_wm_threshold,
                                )
                            except Exception as ex:
                                print(f"[hosts] webmin failure-record "
                                      f"failed for {h.get('id')!r}: {ex}")
            hosts_map: dict = result.get("hosts") or {} if isinstance(result, dict) else {}
            if hosts_map:
                stats = next(iter(hosts_map.values()))
                _merge_best(merged, stats)
                providers_hit.append("webmin")

    # Ping — fifth provider, runs LAST in the merge chain. Only
    # consults the LATEST stored sample (the sampler does the actual
    # probing on its own cadence). When this host is opted-out
    # (``hosts_config[].ping.enabled == False``), we deliberately skip —
    # no row, no chip, no banner.
    _raw_pcfg = h.get("ping")
    pcfg: dict = _raw_pcfg if isinstance(_raw_pcfg, dict) else {}
    if "ping" in active and pcfg.get("enabled"):
        from logic import ping_sampler as _ping_sampler
        from logic import ping as _ping_mod
        recent = _ping_sampler.last_samples(h["id"], limit=1)
        if recent:
            last = recent[0]
            stats = _ping_mod.to_host_stats({
                "alive": last.get("alive"),
                "rtt_ms": last.get("rtt_ms"),
                "loss_pct": last.get("loss_pct"),
            })
            if stats:
                _merge_best(merged, stats)
                # Count ping as a "provider hit" whenever we got a sample
                # back, regardless of alive/down. The alive flag is
                # surfaced separately on the row so the SPA can render
                # the right chip + status colour. Pre-fix this only
                # appended when alive=True, which meant a ping-only host
                # that was currently DOWN got filtered out as "no
                # provider returned data" and rendered grey/unconfigured
                # instead of the red "down" the operator expected.
                providers_hit.append("ping")

    # Port-scan history fold-in — populate `merged.detected_ports`
    # from `host_port_scans`. Does NOT bump `providers_hit`: port-scan
    # is a curated-config / on-demand surface, not a continuous
    # monitoring provider, and the providers count below the host
    # name on the drawer was reading "+1" on every host with prior
    # scan rows because of an earlier inclusion of "port_scan" here.
    # The detected-ports chip strip + the optional port_scan_refresh
    # schedule kind are surfaced via their own UI paths; the merged-
    # row `providers` array stays scoped to the live-telemetry
    # providers (Beszel / NE / Pulse / Webmin / SNMP / Ping).
    _populate_detected_ports(h["id"], merged)

    # Re-derive `host_mem_percent` and `host_disk_percent` from the
    # MERGED used + total values so the percent stays consistent with
    # the bytes regardless of which providers contributed which field.
    # Without this: SNMP reports `host_mem_percent: 90.87` (computed
    # from `total - free`, FreeBSD-naive — doesn't subtract cache /
    # inactive), while NE reports `host_mem_used: 13.58 GB` /
    # `host_mem_total: 16.56 GB` (FreeBSD-aware: free + inactive +
    # laundry + cache as available). NE comes AFTER SNMP in the merge
    # order, but NE's `extract_stats` doesn't emit `host_mem_percent`
    # — only used/total/avail — so SNMP's percent survives the merge
    # while NE's bytes win, producing an inconsistent merged shape:
    # 90% in `host_mem_percent` (used in the host card label) vs
    # ~82% the chart history shows (sampler computes from NE's
    # bytes). Reported on a FreeBSD / OPNsense host where the outside
    # card reads "90% (13 GB / 15 GB)" while the drawer's memory chart
    # reads 82% — same data, two answers. Recomputing from the
    # merged bytes gives one truth.
    _t = merged.get("host_mem_total") or 0
    _u = merged.get("host_mem_used") or 0
    if _t > 0 and _u > 0:
        merged["host_mem_percent"] = round(min(100.0, (_u / _t) * 100.0), 2)
    # Post-merge mounts-aggregate override — handles the case where
    # ANY provider supplied a `mounts[]` list (Beszel EFS, Pulse via
    # PVE guest-agent fsinfo, NE per-mount, Webmin per-mount) that
    # sums to substantially MORE than the merged `host_disk_total`.
    # When that happens, the operator-visible reality is the per-mount
    # list (which the SPA renders as DISKS rows), so the chip aggregate
    # should agree with it. Catches the TrueNAS case where Pulse's
    # guest-agent fsinfo populates `mounts[/mnt/POOL1, /]` with 5.3 TB
    # total but `host_disk_total` is the agent's overlay disk
    # (~802 GiB) — the chip used to read 0.1% used because the
    # numerator was the overlay's used and the denominator was its
    # total, while the per-mount list correctly showed 84% used on
    # the actual data pool. Threshold: only override when the mounts
    # sum is at least 1.5× the existing total — protects against
    # spurious mount entries (loop devices, tmpfs leaking through)
    # tipping the aggregate by a small margin. The mounts list `d` /
    # `du` fields are GiB floats per the schema in `_flatten_efs` and
    # `_pulse_mounts`; convert × 1024^3 to bytes for the byte-totals.
    mounts = merged.get("mounts") or []
    if isinstance(mounts, list) and mounts:
        gib = 1024 ** 3
        m_total = 0.0
        m_used = 0.0
        for _m in mounts:
            if not isinstance(_m, dict):
                continue
            try:
                m_total += float(_m.get("d") or 0)
                m_used += float(_m.get("du") or 0)
            except (TypeError, ValueError):
                continue
        if m_total > 0:
            m_total_b = int(m_total * gib)
            m_used_b = int(m_used * gib)
            cur_total = int(merged.get("host_disk_total") or 0)
            # Override when the mounts aggregate is meaningfully bigger
            # than the merged total. 1.5× threshold catches the
            # overlay-vs-real-disk case (5300/802 ≈ 6.6×) without
            # flapping on small per-mount additions.
            if cur_total <= 0 or m_total_b > cur_total * 1.5:
                merged["host_disk_total"] = m_total_b
                merged["host_disk_used"] = m_used_b
                merged["host_disk_free"] = max(0, m_total_b - m_used_b)
                try:
                    # Canonical resolved-target pattern — surface the
                    # `address` field next to the curated id so
                    # operators tracing "which actual host is this?"
                    # don't have to cross-reference the host_id →
                    # alias mapping. Same shape as the sampler log
                    # lines (host_net_sampler / host_metrics_sampler
                    # / ping_sampler / host_baseline_sampler) per the
                    # canonical "Resolved-target hints in every
                    # sampler log line" convention.
                    _addr = (h.get("address") or "").strip()
                    _target = f" target={_addr}" if _addr else ""
                    print(
                        f"[hosts] mounts-aggregate {h.get('id')!r}{_target}: "
                        f"replaced host_disk_total={cur_total} "
                        f"with mounts-summed {m_total_b} "
                        f"({m_total:.1f} GiB total / {m_used:.1f} GiB used "
                        f"across {len(mounts)} mounts)"
                    )
                except (ValueError, TypeError, AttributeError):
                    pass
    _t = merged.get("host_disk_total") or 0
    _u = merged.get("host_disk_used") or 0
    if _t > 0 and _u > 0:
        merged["host_disk_percent"] = round(min(100.0, (_u / _t) * 100.0), 2)

    # HTTP / TLS / DNS probe — seventh host-stats provider. Reads
    # persisted samples from `host_http_samples` via the shared helper
    # so this endpoint AND `/api/hosts/list` (skeleton) surface the
    # same on-disk state without duplicating the SELECT. No live probe
    # here — the lifespan sampler owns the wire calls; this endpoint
    # just stamps the latest persisted result onto the merged dict.
    # Per-host enable gate AND master toggle: the row's
    # `http_probe.enabled` must be True AND `http_probe_enabled` master
    # setting must be on. When neither is set, skip entirely so the
    # merged dict doesn't carry stale fields from a previous enrolment.
    # Per-host in-memory cache mirrors the Webmin / SNMP pattern: a
    # success TTL avoids re-running the SELECT on every fan-out call,
    # a shorter failure TTL keeps recovery snappy. Reads via
    # `tuning_int` per-call so an Admin → Config edit lands without
    # restart.
    try:
        _raw_http = h.get("http_probe")
        _http_cfg: dict = _raw_http if isinstance(_raw_http, dict) else {}
        _http_master = get_setting_bool(Settings.HTTP_PROBE_ENABLED)
        if (
            "http_probe" in active
            and _http_master
            and _http_cfg.get("enabled") is True
            and not _is_provider_paused(h["id"], "http_probe")
        ):
            from logic.host_http_sampler import populate_host_http_merge
            now_http = time.time()
            http_success_ttl = tuning.tuning_int(Tunable.HTTP_PROBE_HOST_CACHE_TTL_SECONDS)
            http_fail_ttl = tuning.tuning_int(Tunable.HTTP_PROBE_HOST_FAIL_CACHE_TTL_SECONDS)
            cache_key = h["id"]
            cached = _http_probe_host_cache.get(cache_key)
            if cached and (now_http - cached[0]) < (http_success_ttl if cached[2] else http_fail_ttl):
                cached_fields: dict = cached[1]
            else:
                pre_keys = {
                    k: merged.get(k) for k in (
                        "host_http_status_ok", "host_http_status_code",
                        "host_http_content_match_ok", "host_http_tls_expires_in_days",
                        "host_http_tls_subject", "host_http_dns_resolved",
                        "host_http_latency_ms", "host_http_error",
                        "host_http_url_count_total", "host_http_url_count_ok",
                        "host_http_urls", "host_http_ts",
                    )
                }
                populate_host_http_merge(h["id"], merged)
                post = {k: merged.get(k) for k in pre_keys}
                # Cache the just-stamped fields so the next fan-out call
                # reuses them. `had_data` flag drives the TTL choice
                # (success = longer reuse, failure = shorter recovery).
                had_data = bool(merged.get("host_http_url_count_total"))
                _http_probe_host_cache[cache_key] = (now_http, post, had_data)
                cached_fields = post
            for k, v in cached_fields.items():
                if v is not None:
                    merged[k] = v
            if merged.get("host_http_url_count_total"):
                providers_hit.append("http_probe")
    except Exception as e:  # noqa: BLE001
        print(f"[hosts] http_probe merge failed for {h.get('id')!r}: {e}")

    # Per-service `last_probe` is stamped by `_shape_host_apps(h)` (which
    # walks the curated `h["services"]` array and reads service_samples)
    # — NOT here. The merged provider dict's service key holds the Beszel
    # systemd rollup, never the curated array, so no per-service stamp
    # belongs on `merged`.

    # Snapshot fallback — when a provider went down mid-session,
    # fill missing host_* fields from the previous gather's persisted
    # snapshot and tag them in `_stale_fields` so the SPA can dim those
    # values. Only fills MISSING fields — live values from this run
    # always win. `apply_host_snapshot_fallback` is a no-op when no
    # snapshot exists for this host.
    try:
        from logic.gather import apply_host_snapshot_fallback as _fallback
        _fallback({h["id"]: merged})
    except Exception as e:  # noqa: BLE001
        print(f"[hosts] snapshot fallback failed for {h.get('id')!r}: {e}")

    # Persist the just-merged dict as the snapshot for this host
    # . Pre-fix, snapshots were only written by the legacy
    # _gather_impl path (the one /api/items uses) which builds
    # nodes_info from Swarm-node hostnames — curated SNMP-only hosts
    # like UPSes / managed switches that aren't Swarm nodes never
    # appeared in nodes_info, so save_host_snapshots never wrote a
    # row for them. Fallback then had nothing to restore when SNMP
    # stopped returning data → operator-reported "UPS card disappears
    # 5 minutes after the last probe even though SNMP says Updated 7m
    # ago". Write the snapshot from the per-host probe path AS WELL
    # so any host that ever has a successful probe gets a fallback
    # source.
    #
    # Gate: persist ONLY when at least one snapshot-
    # eligible field is LIVE (not from fallback). Pre-fix the gate
    # was "any meaningful host_* field present" — that fired even
    # when EVERY field came from `apply_host_snapshot_fallback`
    # above, so the snapshot's `ts` kept refreshing to "now" on every
    # 15s drawer poll even when no live data had been recorded for
    # minutes. Operator-reported: printer card freshness label
    # ("3m ago") disagreed with the SNMP chart freshness label
    # ("6m ago") because the snapshot ts kept getting touched while
    # the underlying samples table (which the chart freshness reads)
    # tracked the actual last-live-probe correctly. Now the snapshot
    # only persists when at least one snapshot-eligible field came
    # from a LIVE provider — i.e. is meaningful AND not in
    # `_stale_fields`. Entirely-fallback merges skip the write so
    # the existing snapshot's `ts` stays at the genuine last-live
    # timestamp.
    try:
        from logic.gather import (
            save_host_snapshots as _save_snaps,
            is_snapshot_key as _snap_key,
        )
        from logic.merge import is_meaningful as _is_mean
        stale_set = set(merged.get("_stale_fields") or [])
        has_live_field = any(
            _snap_key(k) and _is_mean(v) and k not in stale_set
            for k, v in merged.items()
        )
        if has_live_field:
            _save_snaps({h["id"]: merged})
    except Exception as e:  # noqa: BLE001
        print(f"[hosts] snapshot save failed for {h.get('id')!r}: {e}")

    # Per-host per-provider sample row counts — surfaces under the
    # "Updated Xs ago" subtitle in the host drawer's enabled-agents
    # chip strip so the user can see at a glance how much history is
    # available per provider for THIS host. Single multi-table SELECT
    # per host so the fan-out cost stays bounded; failures swallowed
    # since this is a UX hint, not load-bearing.
    try:
        merged["provider_sample_counts"] = _provider_sample_counts(h["id"])
    except Exception as e:  # noqa: BLE001
        print(f"[hosts] provider_sample_counts failed for {h.get('id')!r}: {e}")
        merged["provider_sample_counts"] = {}

    # Per-provider effective sampler interval (seconds) — surfaces as
    # the third subtitle line below the chip ("Every Ns" / "Every Nm").
    # Resolves the "0 = inherit" sentinel each sampler uses so the user
    # sees the actual applied value, not the raw tunable. Host-id is
    # accepted for future per-host override knobs; current intervals
    # are global.
    try:
        merged["provider_sample_intervals"] = _provider_sample_intervals(h["id"])
    except Exception as e:  # noqa: BLE001
        print(f"[hosts] provider_sample_intervals failed for {h.get('id')!r}: {e}")
        merged["provider_sample_intervals"] = {}

    # Per-provider NEWEST sample ts — drives the SPA's "freshness
    # disagreement ⚠" overlay on a chip whose `last_ok_ts` lags this
    # value by significantly more than the matching sample interval.
    # Operator-debug surface for future per-(provider, host)
    # `host_provider_last_ok` stamping drift; absent ⇒ no overlay.
    try:
        merged["provider_sample_newest_ts"] = _provider_sample_newest_ts(h["id"])
    except Exception as e:  # noqa: BLE001
        print(f"[hosts] provider_sample_newest_ts failed for {h.get('id')!r}: {e}")
        merged["provider_sample_newest_ts"] = {}

    # Drift-from-baseline enrichment. Reads the cached
    # `host_baselines` row for THIS host and classifies each live
    # metric (CPU% / mem% / disk% / ping RTT) as ▲ / ▼ / ━ vs the
    # 30-day rolling median ± IQR. Returns {} when no baseline has
    # been computed yet (<50 samples in the window OR sampler hasn't
    # run yet) — the SPA hides the chip in that case. Cheap read
    # (single SELECT keyed on host_id); failures swallowed.
    try:
        from logic import host_baseline as _baseline
        merged["drift"] = _baseline.host_drift_for_api(h["id"], merged)
    except Exception as e:  # noqa: BLE001
        print(f"[hosts] drift compute failed for {h.get('id')!r}: {e}")
        merged["drift"] = {}

    return merged, providers_hit


def _provider_sample_intervals(host_id: str) -> dict:
    """Return ``{<provider>: int seconds}`` for each curated provider's
    effective sampler cadence. Resolves the "0 = inherit" sentinel each
    sampler uses so the value matches what the operator's sampler is
    ACTUALLY ticking at, not the raw tunable. Provider names match
    `agent.name` in the SPA's `hostEnabledAgents`.

    `host_id` is accepted for future per-host override knobs; current
    intervals are global, so the value is identical across hosts in
    this implementation.
    """
    _ = host_id  # placeholder for future per-host override resolution
    from logic import tuning as _tuning
    out: dict[str, int] = {}
    global_iv = max(30, int(_tuning.tuning_int(Tunable.STATS_SAMPLE_INTERVAL_SECONDS)) or 300)
    # Each provider's tunable: 0 = inherit global, > 0 = explicit override.
    # The sampler floor (typically 10s or 30s) is also applied so the
    # surfaced value matches what the sampler loop actually sleeps for.
    inheritors = (
        ("ping", Tunable.PING_INTERVAL_SECONDS, 10),
        ("snmp", Tunable.SNMP_SAMPLE_INTERVAL_SECONDS, 30),
        ("beszel", Tunable.BESZEL_SAMPLE_INTERVAL_SECONDS, 30),
        ("pulse", Tunable.PULSE_SAMPLE_INTERVAL_SECONDS, 30),
        ("node_exporter", Tunable.NODE_EXPORTER_SAMPLE_INTERVAL_SECONDS, 30),
        # Probe-result providers — also follow the inherit-or-override
        # contract via their dedicated sample-interval tunables. Floor
        # matches each sampler's own ``max(floor, raw)`` clamp so the
        # surfaced value is what the sampler loop actually sleeps for.
        ("http_probe", Tunable.HTTP_PROBE_SAMPLE_INTERVAL_SECONDS, 30),
        ("service_probe", Tunable.SERVICE_PROBE_SAMPLE_INTERVAL_SECONDS, 30),
    )
    for name, key, floor in inheritors:
        try:
            raw = int(_tuning.tuning_int(key) or 0)
        except (ValueError, TypeError, KeyError):
            raw = 0
        effective = max(floor, raw) if raw > 0 else global_iv
        out[name] = effective
    # Webmin has no dedicated sample-interval knob — its sampler shares
    # the global cadence directly (see logic/host_webmin_sampler.py).
    out["webmin"] = global_iv
    return out


def _provider_sample_counts(host_id: str) -> dict:
    """Return ``{<provider>: count}`` for the curated provider lineup,
    keyed by the same provider names ``hostEnabledAgents`` emits on the
    SPA side. Each value is the raw row count in the matching
    `host_<provider>_samples` table (or `ping_samples` / etc.) for
    THIS host. Best-effort — missing tables (fresh deploy, schema not
    yet created) yield 0; never raises.
    """
    out: dict[str, int] = {}
    # `(provider_name, sql)` pairs. Provider name matches `agent.name`
    # in `hostEnabledAgents`. SQL is parameterised on `host_id`.
    queries = (
        ("ping", "SELECT COUNT(*) FROM ping_samples WHERE host_id = ?"),
        ("snmp", "SELECT COUNT(*) FROM host_snmp_samples WHERE host_id = ?"),
        ("beszel", "SELECT COUNT(*) FROM host_beszel_samples WHERE host_id = ?"),
        ("pulse", "SELECT COUNT(*) FROM host_pulse_samples WHERE host_id = ?"),
        ("webmin", "SELECT COUNT(*) FROM host_webmin_samples WHERE host_id = ?"),
        ("node_exporter", "SELECT COUNT(*) FROM host_metrics_samples WHERE host_id = ?"),
        # Probe-result providers — same per-host count surface as the
        # six telemetry providers above. host_http_samples carries one
        # row per (host, url, ts); service_samples carries one row per
        # (host, service_idx, ts).
        ("http_probe", "SELECT COUNT(*) FROM host_http_samples WHERE host_id = ?"),
        ("service_probe", "SELECT COUNT(*) FROM service_samples WHERE host_id = ?"),
    )
    try:
        with db_conn() as c:
            for name, sql in queries:
                try:
                    row = c.execute(sql, (host_id,)).fetchone()
                    out[name] = int(row[0]) if row else 0
                except (sqlite3.Error, ValueError, TypeError):
                    # Missing table on fresh deploy / sampler never ran.
                    out[name] = 0
    except (sqlite3.Error, OSError):
        return {}
    return out


def _provider_sample_newest_ts(host_id: str) -> dict:
    """Return ``{<provider>: epoch_seconds}`` — newest sample ts per
    provider for THIS host. Companion to ``_provider_sample_counts`` +
    ``_provider_sample_intervals``. Used by the SPA's per-provider
    chip to surface a ⚠ overlay when the chip's `last_ok_ts` lags the
    newest sample timestamp by significantly more than the configured
    sample interval — proves the sampler IS writing but the per-
    (provider, host) `host_provider_last_ok` stamp is going stale
    separately (operator-debug surface for future per-provider
    plumbing drift). Best-effort; missing tables → omitted key
    (drives the SPA's gate cleanly).
    """
    out: dict[str, int] = {}
    queries = (
        ("ping", "SELECT MAX(ts) FROM ping_samples WHERE host_id = ?"),
        ("snmp", "SELECT MAX(ts) FROM host_snmp_samples WHERE host_id = ?"),
        ("beszel", "SELECT MAX(ts) FROM host_beszel_samples WHERE host_id = ?"),
        ("pulse", "SELECT MAX(ts) FROM host_pulse_samples WHERE host_id = ?"),
        ("webmin", "SELECT MAX(ts) FROM host_webmin_samples WHERE host_id = ?"),
        ("node_exporter", "SELECT MAX(ts) FROM host_metrics_samples WHERE host_id = ?"),
        ("http_probe", "SELECT MAX(ts) FROM host_http_samples WHERE host_id = ?"),
        ("service_probe", "SELECT MAX(ts) FROM service_samples WHERE host_id = ?"),
    )
    try:
        with db_conn() as c:
            for name, sql in queries:
                try:
                    row = c.execute(sql, (host_id,)).fetchone()
                    if row and row[0] is not None:
                        out[name] = int(row[0])
                except (sqlite3.Error, ValueError, TypeError):
                    pass
    except (sqlite3.Error, OSError):
        return {}
    return out


# True when a host id matches a Swarm node hostname (long-form OR
# short-form). Used to gate the `docker_node` field — non-Swarm hosts
# (VMs / appliances / routers / 5G modems) get an empty value so the
# drawer's misleading "Docker node: <id>" row hides for them.
def _is_swarm_node(host_id) -> bool:
    if not host_id:
        return False
    hid = str(host_id).strip().lower()
    if not hid:
        return False
    short = hid.split(".", 1)[0]
    for n in (_cache.get("nodes") or {}).values():
        if not n:
            continue
        ns = str(n).strip().lower()
        if not ns:
            continue
        if ns == hid or ns == short or ns.split(".", 1)[0] == hid \
            or ns.split(".", 1)[0] == short:
            return True
    return False


# Module-level asset-index cache, keyed on the cache file's mtime so
# we re-build only when the on-disk snapshot actually changes. Hot
# path: every `_shape_host_api_row` call. Cold path: refresh adds
# ~10ms (file read + dict build).
_asset_idx_cache: dict = {"mtime": None, "index": {}}


def _resolve_asset_for_host(cn) -> Optional[dict]:
    """Look up the cached asset row for a host's custom_number and
    return the compact `shape_asset` dict (or None when no match).

    Re-reads the cache file when its mtime advances, otherwise reuses
    the indexed map. Resilient to a missing / unreadable cache —
    returns None on any error so `_shape_host_api_row` can still
    build a row for hosts whose asset data isn't available yet.

    Sentinel handling: ``mtime`` is ``None`` for "no readable cache
    file yet". Comparing a real mtime (any float, including 0.0) to
    None is always non-equal, so we rebuild on the first successful
    read; subsequent calls with a missing file stay at ``mtime=None``
    and DO NOT rebuild the empty index every call.
    """
    if cn is None:
        return None
    try:
        cn_int = int(cn)
    except (TypeError, ValueError):
        return None
    from logic import asset_inventory as _ai
    try:
        mtime: Optional[float] = os.path.getmtime(_ai.DEFAULT_CACHE_PATH)
    except OSError:
        mtime = None
    if mtime != _asset_idx_cache["mtime"]:
        try:
            cache = _ai.load_cache()
            _asset_idx_cache["index"] = _ai.index_by_custom_number(cache.get("assets") or [])
        except (OSError, ValueError, KeyError, AttributeError):
            _asset_idx_cache["index"] = {}
        _asset_idx_cache["mtime"] = mtime
    raw = _asset_idx_cache["index"].get(cn_int)
    return _ai.shape_asset(raw) if raw else None


def _resolve_ping_target(h: dict) -> Optional[str]:
    """Mirror of `logic.ping_sampler._curated_ping_hosts`'s target chain.

    Returns the resolved hostname / IP that `probe_ping` will actually
    use, or None when ping isn't enabled on the row. Surfacing this in
    the API row (`ping_target`) lets the SPA's `?` info-bubble tooltip
    name the actual probe target instead of the curated host_id (which
    is often a label like "ftth" that doesn't resolve via DNS).

    Resolution chain (FIRST non-empty wins):
      1. `address` (curated dedicated probe target — independent of
         any provider, operator-set as "the LAN address for this host")
      2. `ping.host` (per-host override on the ping provider)
      3. `ssh.fqdn` (per-host SSH FQDN — most curated rows have this)
      4. `ssh.host` (alternate SSH-target spelling, legacy)
      5. `h.id` (last-resort fallback)

    The curated `url` field is DELIBERATELY excluded — it carries the
    clickable web-UI link the operator wants to surface on the host
    card. Probing it would target the public service relay instead of
    the LAN host (wrong data + privacy concern).

    The `address` field is the canonical dedicated probe target —
    independent of any provider so disabling SNMP / ping / SSH never
    leaves the other probes without a target. Operators set it in
    Admin → Hosts. If left blank, the chain falls through to provider-
    specific overrides then the bare host_id.
    """
    _raw_ping_cfg = h.get("ping")
    ping_cfg: dict = _raw_ping_cfg if isinstance(_raw_ping_cfg, dict) else {}
    if not bool(ping_cfg.get("enabled", False)):
        return None
    _raw_ssh_cfg = h.get("ssh")
    ssh_cfg: dict = _raw_ssh_cfg if isinstance(_raw_ssh_cfg, dict) else {}
    candidate = (
        (h.get("address") or "").strip()
        or (ping_cfg.get("host") or "").strip()
        or (ssh_cfg.get("fqdn") or "").strip()
        or (ssh_cfg.get("host") or "").strip()
        or (h.get("id") or "").strip()
    )
    return candidate or (h.get("id") or "")


def _is_host_unconfigured(
    active: Optional[Iterable[str]],
    active_set: frozenset[str],
    h: dict,
    *,
    ping_enabled: bool,
    snmp_mapped: bool,
    any_provider_enabled: bool,
) -> bool:
    """Decide whether a curated host has NO globally-enabled provider
    mapped to one of its per-row fields. Extracted from the
    `_shape_host_api_row` host-status fallthrough chain so the nested-
    paren boolean expression doesn't trip PyCharm's continuation-indent
    inspection.

    Two branches: when caller supplies `active` (the canonical case),
    each per-row field must align with a globally-enabled provider for
    the field to count as "mapped". When `active is None` (legacy
    callers), fall back to the whole-row OR-chain.
    """
    if active is not None:
        return not (
            ("beszel" in active_set and (h.get("beszel_name") or "").strip())
            or ("pulse" in active_set and (h.get("pulse_name") or "").strip())
            or ("webmin" in active_set and (h.get("webmin_name") or "").strip())
            or ("node_exporter" in active_set and (h.get("ne_url") or "").strip())
            or ("ping" in active_set and ping_enabled)
            or ("snmp" in active_set and snmp_mapped)
        )
    # Legacy gate — back-compat callers see the whole-row OR-chain.
    return (not any_provider_enabled) or not (
        (h.get("beszel_name") or "").strip()
        or (h.get("pulse_name") or "").strip()
        or (h.get("webmin_name") or "").strip()
        or (h.get("ne_url") or "").strip()
        or ping_enabled
        or snmp_mapped
    )


def _sync_host_stats_source(provider: str, enabled: bool) -> None:
    """Add or remove `provider` from the `host_stats_source` CSV
    setting so the merge gate (which requires both the per-provider
    master flag AND the CSV membership) stays consistent with the
    operator-visible toggle.

    Without this sync the operator could flip `http_probe_enabled` or
    `service_probe_enabled` ON in Admin → Providers, see the master
    switch confirmed, but the merge gate would still skip the provider
    because the CSV didn't include the token. The host drawer card
    then renders empty + the chip strip never gets the provider — a
    "looks right but doesn't work" failure mode.

    Called from `api_set_settings` whenever an http_probe / service_probe
    master toggle landed. Idempotent: re-adding an existing token is a
    no-op; removing one that isn't there is a no-op.
    """
    token = provider.strip().lower()
    if not token:
        return
    current_raw = (get_setting(Settings.HOST_STATS_SOURCE) or "").strip()
    parts: list[str] = []
    for t in current_raw.split(","):
        t_clean = t.strip().lower()
        if t_clean and t_clean != "none" and t_clean not in parts:
            parts.append(t_clean)
    if enabled:
        if token not in parts:
            parts.append(token)
    else:
        if token in parts:
            parts = [p for p in parts if p != token]
    normalized = ",".join(sorted(parts)) if parts else "none"
    if normalized != current_raw:
        set_setting(Settings.HOST_STATS_SOURCE, normalized)


# Module-level TTL'd cache for `_shape_host_apps`'s catalog lookups.
# Without this, the per-call `catalog_cache` in `_shape_host_apps` paid
# one DB hit per unique catalog_id for EVERY host's call — a fleet
# where 8 hosts all run Plex (catalog_id=N) would do the same
# get_catalog_by_id(N) call 8 times per `/api/hosts/list` fan-out.
# The TTL is intentionally short (5s — well under any reasonable poll
# cadence) so a catalog edit propagates within one poll cycle, but
# the SSE `settings:updated` event from `seed_builtins` invalidates
# explicitly so re-seed shows up immediately.
_SHAPE_HOST_APPS_CATALOG_TTL_SECONDS: float = 5.0
_shape_host_apps_catalog_cache: dict = {"ts": 0.0, "by_id": {}}


def _invalidate_shape_host_apps_catalog_cache() -> None:
    """Wipe the catalog cache. Called from catalog write paths
    (catalog CRUD + seed_builtins) so the next `_shape_host_apps` call
    rebuilds from fresh DB state. SSE consumers also bump on
    `settings:updated` so a cross-tab edit invalidates here too."""
    _shape_host_apps_catalog_cache["ts"] = 0.0
    _shape_host_apps_catalog_cache["by_id"] = {}


def _get_shape_host_apps_catalog_map() -> dict:
    """Return a `{catalog_id: catalog_dict|None}` map for use inside
    `_shape_host_apps`. Cached for ``_SHAPE_HOST_APPS_CATALOG_TTL_SECONDS``;
    refilled on TTL miss by walking `list_catalog()` once + materialising
    a dict so subsequent per-chip lookups are O(1) without DB hits.
    Negative entries (catalog_id pointing at a deleted template) are
    cached as ``None`` to avoid re-querying dead FKs every tick.
    """
    import time as _time
    now = _time.time()
    cache_ts = float(_shape_host_apps_catalog_cache.get("ts") or 0.0)
    if (now - cache_ts) < _SHAPE_HOST_APPS_CATALOG_TTL_SECONDS:
        by_id_cached = _shape_host_apps_catalog_cache.get("by_id")
        if isinstance(by_id_cached, dict):
            return by_id_cached
    try:
        from logic.service_catalog import list_catalog as _list_catalog
        rows = _list_catalog() or []
    except Exception as e:  # noqa: BLE001
        print(f"[apps] list_catalog refresh skipped: {e}")
        return _shape_host_apps_catalog_cache.get("by_id") or {}
    by_id: dict = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        # Narrow Any|None → Optional[int] via the canonical coercion helper
        # (same as every other JSON/dict-cell int boundary in the Apps code)
        # so the type checker sees a concrete int before the dict key write.
        cid = _coerce_int_local(r.get("id"))
        if cid is None:
            continue
        by_id[cid] = r
    _shape_host_apps_catalog_cache["ts"] = now
    _shape_host_apps_catalog_cache["by_id"] = by_id
    return by_id


def _shape_host_apps(h: dict, *,
                     _latest_map: Optional[dict] = None,
                     _per_port_map: Optional[dict] = None) -> list[dict]:
    """Return the Apps feature's per-chip array for the API row.

    Walks ``h["services"]`` (operator-curated chip array on the
    ``hosts_config[]`` entry), stamps each entry with:

    - ``service_idx``  — stable position in the source list; consumed
      by the manual probe endpoint and per-port history queries.
    - ``last_probe``   — most-recent sample from ``service_samples``
      for this (host_id, service_idx). ``None`` when no sample yet.
    - ``status``       — derived ``up`` / ``down`` / ``unknown`` from
      ``last_probe.alive`` (None when no sample).
    - ``catalog``      — resolved catalog template dict when the chip
      carries ``catalog_id``; ``None`` otherwise.

    Source list entries are NOT mutated — the returned shape is a
    fresh dict per chip so frontend mutations during render can't
    propagate back. Empty list when ``h["services"]`` is missing /
    not a list. Catalog lookup is cached per call so a 30-chip host
    doesn't hit the DB 30 times.
    """
    chips_raw = h.get("services")
    if not isinstance(chips_raw, list) or not chips_raw:
        return []
    host_id = (h.get("id") or "").strip()
    if not host_id:
        return []
    # when bulk maps are passed (api_hosts_list's bulk loop), look
    # the host's latest + per-port rows up from them — no per-host DB hit;
    # otherwise fall through to the per-host helpers (detail path).
    if _latest_map is not None:
        latest = _latest_map.get(host_id, {}) or {}
        _latest_per_port = None  # bulk path uses _per_port_map instead
    else:
        try:
            from logic.service_sampler import (
                latest_for_host as _latest_for_host,
                latest_per_port_for_host as _latest_per_port,
            )
            latest = _latest_for_host(host_id)
        except Exception as e:  # noqa: BLE001
            print(f"[apps] latest_for_host({host_id!r}) skipped: {e}")
            latest = {}
            _latest_per_port = None  # type: ignore[assignment]
    # Catalog lookup map — preloaded once for the WHOLE request (not
    # per-host call) via the module-level TTL cache. Pre-fix every call
    # to `_shape_host_apps` ran `get_catalog_by_id(cid)` per unique chip
    # catalog_id; the fan-out's N-hosts × M-chips paid M DB hits per host
    # even when every host shared the same Plex/Sonarr templates. The
    # TTL cache amortises to ONE list_catalog() call per 5s window.
    catalog_by_id = _get_shape_host_apps_catalog_map()
    out: list[dict] = []
    for idx, chip in enumerate(chips_raw):
        if not isinstance(chip, dict):
            continue
        sample = latest.get(idx) if isinstance(latest, dict) else None
        status = "unknown"
        if isinstance(sample, dict):
            status = "up" if sample.get("alive") else "down"
        # `_coerce_int_local` (imported at module top from
        # service_catalog) narrows the Any-typed `catalog_id` cell to
        # Optional[int] without the legacy try/except + `in (None, "")`
        # ladder that type checkers couldn't see through.
        cid_int = _coerce_int_local(chip.get("catalog_id"))
        # O(1) lookup in the preloaded map. Negative-cached None is
        # returned for FKs pointing at a deleted template — same
        # downstream rendering as a chip without catalog_id.
        catalog_block: Optional[dict] = catalog_by_id.get(cid_int) if cid_int else None
        # Per-port latest results — populated only when this chip is
        # multi-port AND the sampler has run at least once for it. Empty
        # list for single-port chips OR multi-port chips with no history.
        port_results: list[dict] = []
        probe_block = chip.get("probe") or {}
        is_multi_port = isinstance(probe_block.get("ports"), list) and bool(probe_block.get("ports"))
        raw_port_results: list = []
        # Original gate preserved: only run when we have SOME per-port source —
        # either the bulk-prefetched map (api_hosts_list path) OR the per-host
        # helper (detail path; None only when the service_sampler import failed).
        if is_multi_port and (_per_port_map is not None or _latest_per_port is not None):
            if _per_port_map is not None:
                # bulk path — pre-fetched per-(host, chip) rows.
                raw_port_results = list(_per_port_map.get((host_id, idx), []))
            elif _latest_per_port is not None:
                try:
                    raw_port_results = _latest_per_port(host_id, idx)
                except Exception as e:  # noqa: BLE001
                    print(f"[apps] latest_per_port({host_id!r}/{idx}) skipped: {e}")
                    raw_port_results = []
            # Per-port display = the chip's CONFIGURED ports merged with the
            # probe-sample HISTORY: every configured port shows (a just-added
            # one as PENDING until the sampler probes it), stale samples for
            # removed/template ports are dropped. Shared with the top-level
            # Apps view via logic.service_catalog.merge_port_results.
            from logic.service_catalog import merge_port_results as _merge_port_results
            port_results = _merge_port_results(probe_block.get("ports"), raw_port_results)
        out.append({
            "service_idx": idx,
            "name": (chip.get("name") or "").strip(),
            "url": (chip.get("url") or "").strip(),
            # Icon resolution: chip override wins; otherwise inherit the
            # catalog template's icon directly so a slug != icon template
            # (e.g. Veeam Server slug 'veeam-server' icon 'veeam') resolves
            # without the frontend having to dig into catalog.icon (which it
            # also does, but stamping here is the robust single source).
            "icon": (chip.get("icon") or (catalog_block or {}).get("icon") or "").strip(),
            "catalog_id": cid_int,
            "catalog": catalog_block,
            "probe": probe_block,
            "status": status,
            "last_probe": sample,
            "port_results": port_results,
        })
    return out


# noinspection PyTypeChecker,PyUnresolvedReferences
def _shape_host_api_row(
    h: dict,
    merged: dict,
    providers_hit: list[str],
    any_provider_enabled: bool = True,
    active: Optional[Iterable[str]] = None,
    *,
    _apps_latest_map: Optional[dict] = None,
    _apps_per_port_map: Optional[dict] = None,
) -> dict:
    """Shape a (curated_host, merged_stats) pair into the wire format.

    ``any_provider_enabled`` — false when NO provider is enabled
    globally (``state.active`` is empty). In that case a host with
    provider fields mapped can't be probed at all, so we report
    `status: 'unconfigured'` instead of `'unknown'` — grey dot, no
    "no data" banner, because there's literally nothing OmniGrid
    could have done. Operators see a clear "configure a provider"
    path instead of a false red alert.

    ``active`` — when supplied (recommended), the set of provider
    names currently enabled globally. Used to intersect the per-row
    field gate so a stale field for a globally-disabled provider
    (e.g. `beszel_name` left over from a prior config when Beszel is
    no longer enabled) does NOT flip the row to `status: "unknown"`.
    Same drift class as the SNMP `enabled` flag gate above, but at
    the per-field-per-provider layer. Legacy bool gate retained for
    back-compat — when ``active`` is None the OLD whole-row OR-chain
    fires (matches pre-fix behaviour).
    """
    s = merged or {}
    # Status precedence (revised — see operator complaint that hosts
    # were marked "down" purely because Beszel was paused/down even
    # when Pulse + node-exporter + Webmin were happily scraping):
    # 1. ANY non-Beszel provider returning data → "up". Beszel's
    #    self-reported status is suggestive but its agent can be
    #    paused/down while the host is still reachable — pulse /
    #    NE / webmin all probe via different paths/ports/protocols
    #    and a successful scrape from any of them proves the host
    #    is alive. SSH and other "is this host reachable" gates
    #    depend on this status, so a single failing provider must
    #    not lock other features out.
    # 2. Beszel's explicit status (with paused → down normalisation)
    #    when Beszel is the ONLY signal we have. Operator pauses
    #    hosts in Beszel deliberately when they're offline; "down"
    #    here reflects reality.
    # 3. Pulse's explicit status as a secondary fallback.
    # 4. "up" if any provider hit at all (covers Beszel-only
    #    hosts where Beszel returned data with no explicit status).
    # 5. "unconfigured" when no provider is mapped/enabled — grey.
    # 6. "unknown" when providers ARE mapped + active but none
    #    answered — surfaced red as a real outage signal.
    beszel_st = s.get("beszel_status")
    if beszel_st == "paused":
        beszel_st = "down"
    pulse_st = s.get("pulse_status")
    # Ping is excluded from `non_beszel_hit` because — unlike the other
    # providers — a ping "hit" doesn't prove the host is alive. Ping IS
    # the alive/down signal, so a ping sample that says alive=False
    # means the host is down. The dedicated ping branch below derives
    # "up" / "down" from `host_ping_alive`; the other providers
    # implicitly mean "alive" when they return data at all.
    # SNMP slots in here as a "real telemetry hit" (alongside pulse /
    # node_exporter / webmin) — when SNMP successfully returns data,
    # the host is alive on the network even if Beszel hasn't reached
    # it yet.
    non_beszel_hit = any(
        p in providers_hit for p in ("pulse", "node_exporter", "webmin", "snmp")
    )
    ping_hit = "ping" in providers_hit
    ping_alive = s.get("host_ping_alive")
    ping_enabled = bool((h.get("ping") or {}).get("enabled", False))
    # SNMP mapping gate — matches the per-host probe path
    # (`_merge_one_host`) and the debug panel's `host_active`
    # computation: SNMP is mapped iff (a) the per-host opt-in flag
    # `snmp.enabled === True` AND (b) at least one resolvable target
    # (`snmp_name` OR shared `address`). Without the `enabled` gate
    # any stale override left in the `snmp` sub-dict (`{community,
    # version, port}` typed once, then the operator unchecked the
    # enable box) makes a row read as "snmp mapped" and the status
    # flips from `unconfigured` (grey, no problem) to `unknown` (red,
    # real outage signal) — false-positive that the operator can only
    # cure by wiping the whole sub-dict in Admin → Hosts.
    snmp_block = h.get("snmp")
    snmp_mapped = bool(
        isinstance(snmp_block, dict)
        and snmp_block.get("enabled") is True
        and (
            (h.get("snmp_name") or "").strip()
            or (h.get("address") or "").strip()
        )
    )
    # Frozenset for the refined gate below. Empty when caller didn't
    # supply `active` — the legacy bool gate handles that branch.
    _active_set: frozenset[str] = frozenset(active) if active is not None else frozenset()
    if non_beszel_hit:
        host_status = "up"
    elif beszel_st in ("up", "down"):
        host_status = beszel_st
    elif pulse_st:
        host_status = pulse_st
    elif ping_hit:
        host_status = "up" if ping_alive else "down"
    elif providers_hit:
        host_status = "up"
    elif s.get("_stale_fields"):
        # Cold-load /api/hosts/list path: probes haven't run yet
        # (providers_hit is empty by design — that endpoint is the
        # fast skeleton, the per-host fan-out via /api/hosts/one
        # fills live status afterwards). The snapshot-fallback at
        # apply_host_snapshot_fallback restored host_* fields from
        # the persisted snapshot AND stamped _stale_fields, which
        # is evidence the previous gather successfully reached this
        # host. Promote status='up' provisionally so the SPA's bar
        # gates (which require h.status === 'up') render snapshot-
        # derived bars + sparklines on cold-load instead of staying
        # empty until the per-host probe lands. The _stale_fields
        # marker stays set so the UI dims the values + tooltips
        # them with "X minutes ago" via the existing stale-rendering
        # pipeline. Pre-fix the gate ALSO required one of four
        # specific host_* fields (cpu / mem_total / disk_total /
        # uptime_s) — a healthy host whose snapshot carried a
        # different subset (identity-only `host_platform` / `host_
        # kernel` / `host_arch`; SNMP-derived metrics under different
        # keys; Webmin's coarser snapshots) fell through to the
        # `else: "unknown"` branch and got flagged as a problem host.
        # The fallback's `_is_meaningful` filter already prevents
        # zero / empty values from polluting `_stale_fields`, so the
        # mere existence of any entry there is sufficient evidence
        # of past liveness. Live status overwrites this on the next
        # /api/hosts/one
        # response.
        host_status = "up"
    elif _is_host_unconfigured(
        active, _active_set, h,
        ping_enabled=ping_enabled,
        snmp_mapped=snmp_mapped,
        any_provider_enabled=any_provider_enabled,
    ):
        host_status = "unconfigured"
    else:
        host_status = "unknown"
    _ping_port_raw = (h.get("ping") or {}).get("port")
    _ping_port: Optional[int] = int(_ping_port_raw) if _ping_port_raw is not None else None
    return {
        "id": h["id"],
        "name": h["id"],
        "host": h["id"],
        # Empty label is INTENTIONAL post-frontend's
        # `hostDisplayName(h)` falls back to the asset inventory's
        # name when this is blank. The previous `or h["id"]` fallback
        # silently overrode the operator's "use asset name" intent on
        # every API response. Pass the literal stored value through.
        "label": h.get("label") or "",
        "beszel_name": h.get("beszel_name") or "",
        "pulse_name": h.get("pulse_name") or "",
        "ne_url": h.get("ne_url") or "",
        # SNMP target alias. Surfaced on the API row so
        # `providerStates(h)` and `hostHasAgent(h)` can decide whether
        # to render the SNMP chip + count this host as having an agent.
        "snmp_name": h.get("snmp_name") or "",
        # Webmin target alias. Surfaced on the API row so the SPA's
        # toolbar `hostsProviderState('webmin')` can gate on
        # "configured for Webmin" rather than "Webmin probe succeeded",
        # which would hide the chip during a hub outage and lose
        # visibility on the real failure. Aligns Webmin with the
        # other `<provider>_name` fields that have always been on the
        # row shape.
        "webmin_name": h.get("webmin_name") or "",
        # Per-host SNMP opt-in flag. The bug: the SPA's
        # SNMP chip iterators were gating on `h.snmp_name` alone, so a
        # host with snmp_name set but `snmp.enabled === false` STILL
        # rendered the SNMP chip on the Hosts page. The frontend gates
        # now read `snmp_enabled === true && snmp_name` per         # explicit opt-in contract.
        "snmp_enabled": bool((h.get("snmp") or {}).get("enabled", False)),
        "url": h.get("url") or "",
        "icon": h.get("icon") or "",
        # Dedicated probe target — surfaced on the API row so the SPA
        # can gate the host-drawer port-scan button on "address is
        # set". Empty value = port-scan disabled with helper toast
        # asking the operator to set it in Admin → Hosts.
        "address": h.get("address") or "",
        "providers": providers_hit or [],
        "status": host_status,
        # Raw per-provider status surfaced so the SPA's `providerStates(h)`
        # helper can mark a chip red when Beszel/Pulse self-reports
        # paused/down even if it returned data (otherwise the chip
        # stays green because the provider was technically "hit").
        "beszel_status": s.get("beszel_status") or "",
        "docker_node": (h["id"] if _is_swarm_node(h.get("id")) else ""),
        "platform": s.get("host_platform") or "",
        "os": s.get("host_os") or "",
        "kernel": s.get("host_kernel") or "",
        "arch": s.get("host_arch") or "",
        "agent": s.get("host_agent") or "",
        "cores": s.get("host_cores") or s.get("host_threads") or 0,
        "threads": s.get("host_threads") or 0,
        "cpu_model": s.get("host_cpu_model") or "",
        "cpu_percent": s.get("host_cpu_percent") or 0,
        "mem_percent": s.get("host_mem_percent") or 0,
        "disk_percent": s.get("host_disk_percent") or 0,
        "mem_used": s.get("host_mem_used") or 0,
        "mem_total": s.get("host_mem_total") or 0,
        "disk_used": s.get("host_disk_used") or 0,
        "disk_total": s.get("host_disk_total") or 0,
        "mounts": s.get("mounts") or [],
        # `network_ifaces` is set further down (the SNMP-extras-aware
        # site at the bottom of this dict literal) — keeping a second
        # identical key here would be a PyCharm "duplicate dict keys"
        # warning AND the second assignment silently wins regardless.
        "bandwidth": s.get("host_bandwidth") or 0,
        "containers": s.get("host_containers") or 0,
        "uptime_s": s.get("host_uptime_s") or 0,
        "boot_ts": s.get("host_boot_ts"),
        "beszel_id": s.get("beszel_id") or "",
        "beszel_updated": s.get("beszel_updated") or "",
        "pulse_kind": s.get("pulse_kind") or "",
        "pulse_vmid": s.get("pulse_vmid") or 0,
        "pulse_node": s.get("pulse_node") or "",
        "pulse_status": s.get("pulse_status") or "",
        "updates_pending": int(s.get("host_updates_pending") or 0),
        "updates_security": int(s.get("host_updates_security") or 0),
        "custom_number": h.get("custom_number"),
        # Asset-inventory snapshot — null when no match. Resolved
        # lazily here (vs. eagerly in the loop above) so each
        # _shape_host_api_row call is self-contained. The cache read
        # is fast (file → JSON) but the index build is O(N), so
        # repeated calls in /api/hosts/one/{id} fanouts pay it once
        # per call. If that becomes a hotspot we can stash the
        # index on the request via FastAPI Depends().
        "asset": _resolve_asset_for_host(h.get("custom_number")),
        # Per-host SSH-enabled flag (opt-in semantics post
        # migration 001). True only when the operator explicitly ticked
        # "Enable SSH for this host" in Admin → Hosts. The drawer's SSH
        # card + common-actions panel render only when this is true.
        "ssh_enabled": bool((h.get("ssh") or {}).get("enabled", False)),
        # Ping. `ping_enabled` is the per-host opt-in flag (the
        # SPA uses it to gate the latency chip + drawer chart). The
        # alive / RTT / loss values come from the merged provider
        # dict — empty when the sampler hasn't run yet OR ping isn't
        # enabled for this host. Booleans coerced safely so a
        # null-from-snapshot doesn't crash the spread.
        "ping_enabled": bool((h.get("ping") or {}).get("enabled", False)),
        # Per-host ping override values surfaced for the SPA's metricSource
        # tooltip — pre-fix the tooltip read "Ping probe (this host)" for
        # every ping-enabled host with no indication of which port /
        # transport was actually being probed. Empty / null = inherit
        # global default. Transport is one of `tcp` / `icmp` / null.
        "ping_port": _ping_port,
        "ping_transport": ((h.get("ping") or {}).get("transport") or None),
        # Resolved ping TARGET — what `logic.ping_sampler._probe_one`
        # actually feeds to `probe_ping`. Resolution chain mirrors
        # `logic.ping_sampler._curated_ping_hosts` EXACTLY: per-host
        # `ping.host` override → `ssh.fqdn` → `ssh.host` → curated
        # `url`'s hostname → curated `id` as last-resort fallback.
        # Pre-fix the chain skipped `ping.host` (highest-priority
        # override) AND the URL-hostname fallback, so the SPA tooltip
        # reported `h.id` (e.g. "ftth") on rows whose actual probe
        # target was the URL's hostname (e.g. "ftth.example.com")
        # parsed from the curated `url` field.
        "ping_target": _resolve_ping_target(h),
        "ping_alive": bool(s.get("host_ping_alive")) if s.get("host_ping_alive") is not None else None,
        # noinspection PyUnboundLocalVariable,PyTypeChecker,PyUnresolvedReferences
        "ping_rtt_ms": (float(_prtt) if (_prtt := s.get("host_ping_rtt_ms")) is not None else None),
        # noinspection PyUnboundLocalVariable,PyTypeChecker,PyUnresolvedReferences
        "ping_loss_pct": (float(_plp) if (_plp := s.get("host_ping_loss_pct")) is not None else None),
        # Load averages (node-exporter primary, Beszel agents emit
        # `la=[1m,5m,15m]` which `extract_stats` now also surfaces here
        # so the load-average chart works for Beszel-only hosts too).
        # Frontend only renders the row when any of the three is > 0.
        "load_1m": float(s.get("host_load_1m") or 0),
        "load_5m": float(s.get("host_load_5m") or 0),
        "load_15m": float(s.get("host_load_15m") or 0),
        # Per-sensor temperatures. `host_temperatures` is a
        # `{sensor: celsius}` dict from the Beszel agent's `stats.t`
        # (only present when the agent exposes thermal data — Pi has
        # `cpu_thermal`, Intel/AMD has `package_id_0`, NVMe has
        # `nvme_composite`). Hosts without thermal sensors get an
        # empty dict and the frontend chart card hides via the
        # length-gate. The whitelist on this row was the reason the
        # field was being silently dropped before — extract_stats
        # produced it but it never reached the SPA without this line.
        "host_temperatures": dict(s.get("host_temperatures") or {}),
        # Per-GPU stats — Beszel agents emit `stats.g` as a per-GPU
        # dict; `_flatten_gpus` normalises into a list of
        # `{index, name, vram_used_bytes, vram_total_bytes,
        # usage_percent, power_watts}`. Empty list when the host has
        # no discrete GPU; frontend GPU chart cards gate on
        # `host_gpus.length > 0`. Same drift class as
        # `host_temperatures` — extract_stats produced the field but
        # it was being silently dropped on the API boundary because
        # this whitelist didn't include it.
        "host_gpus": list(s.get("host_gpus") or []),
        # Service summary — Beszel agents that run with the
        # systemd extension emit a list of service objects. The
        # extractor normalises into `{total, failed, failed_names}`.
        # Hosts whose agent doesn't track services get
        # `{total: 0, failed: 0, failed_names: []}` and the drawer
        # badge gates on `services.total > 0` to hide cleanly.
        "services": (s.get("host_services") or {"total": 0, "failed": 0, "failed_names": []}),
        # DMI / hardware identity (node-exporter only — Linux /
        # FreeBSD with the DMI collector). Empty strings = no DMI.
        "dmi_vendor": (s.get("host_dmi_vendor") or ""),
        "dmi_product": (s.get("host_dmi_product") or ""),
        "dmi_serial": (s.get("host_dmi_serial") or ""),
        "dmi_bios_version": (s.get("host_dmi_bios_version") or ""),
        # SNMP vendor-specific fields. All of
        # these are populated by `extract_vendor_info` only when the
        # corresponding vendor MIB returned data — non-vendor hosts get
        # empty / None / 0 here and the frontend cards gate on the
        # presence of the field so they don't render empty. Without
        # this whitelist the fields were silently dropped on the API
        # boundary (same drift class as the host_temperatures fix
        # earlier in this row — extract_stats produced them but they
        # never reached the SPA).
        # Universal identity (Dell / Cisco / APC / Synology / printer):
        "host_model": s.get("host_model") or "",
        "host_serial": s.get("host_serial") or "",
        "host_firmware": s.get("host_firmware") or "",
        "host_health": s.get("host_health") or "",
        "host_contact": s.get("host_contact") or "",
        "host_location": s.get("host_location") or "",
        # noinspection PyUnboundLocalVariable,PyTypeChecker,PyUnresolvedReferences
        "host_temp_c": (float(_htc) if (_htc := s.get("host_temp_c")) is not None else None),
        "host_upgrade_status": s.get("host_upgrade_status") or "",
        # per-core CPU + UCD memory breakdown for the new
        # SNMP time-series charts. Empty list / 0 when the host
        # didn't return UCD or hrProcessorLoad walks; frontend gate
        # on length so non-SNMP hosts don't see the cards.
        "host_cpu_per_core": list(s.get("host_cpu_per_core") or []),
        "host_mem_buffers": int(s.get("host_mem_buffers") or 0),
        "host_mem_cached": int(s.get("host_mem_cached") or 0),
        "host_mem_free": int(s.get("host_mem_free") or 0),
        # APC PowerNet-MIB UPS. Present only when the
        # host responded to upsBasicIdentModel / upsBasicOutputStatus.
        "host_ups_status": s.get("host_ups_status") or "",
        # noinspection PyUnboundLocalVariable,PyTypeChecker,PyUnresolvedReferences
        "host_battery_percent": (float(_hbp) if (_hbp := s.get("host_battery_percent")) is not None else None),
        # noinspection PyUnboundLocalVariable,PyTypeChecker,PyUnresolvedReferences
        "host_battery_runtime_s": (int(_hbrs) if (_hbrs := s.get("host_battery_runtime_s")) is not None else None),
        # noinspection PyUnboundLocalVariable,PyTypeChecker,PyUnresolvedReferences
        "host_battery_temp_c": (float(_hbtc) if (_hbtc := s.get("host_battery_temp_c")) is not None else None),
        "host_battery_status": s.get("host_battery_status") or "",
        # noinspection PyUnboundLocalVariable,PyTypeChecker,PyUnresolvedReferences
        "host_load_percent": (float(_hlp) if (_hlp := s.get("host_load_percent")) is not None else None),
        # Printer-MIB. Empty list / 0 / "" → frontend cards hide.
        "printer_page_count": int(s.get("printer_page_count") or 0),
        "printer_supplies": list(s.get("printer_supplies") or []),
        "printer_console_msg": s.get("printer_console_msg") or "",
        # Dell server-health. Populated by
        # `extract_vendor_info` only when the SNMP probe walked back
        # non-empty DELL-RAC-MIB rows — non-Dell agents return empty
        # lists / 0 / "" and the SPA's "Server health" card render
        # gate hides cleanly. Same drift class as `host_temperatures`
        # / `host_gpus` above: extract_vendor_info populated these,
        # the snapshot fallback restored them, the SPA's
        # `CURATED_REFRESH_FIELDS` whitelist tracked them, but they
        # never reached the SPA without this explicit row entry —
        # which is exactly what the operator hit (the card never
        # rendered for their iDRAC host).
        "host_dell_fans": list(s.get("host_dell_fans") or []),
        "host_dell_temps": list(s.get("host_dell_temps") or []),
        "host_dell_psus": list(s.get("host_dell_psus") or []),
        "host_dell_voltages": list(s.get("host_dell_voltages") or []),
        "host_dell_amperages": list(s.get("host_dell_amperages") or []),
        "host_dell_phys_disks": list(s.get("host_dell_phys_disks") or []),
        "host_dell_virt_disks": list(s.get("host_dell_virt_disks") or []),
        # noinspection PyUnboundLocalVariable,PyTypeChecker,PyUnresolvedReferences
        "host_dell_power_watts": (float(_hdpw) if (_hdpw := s.get("host_dell_power_watts")) is not None else None),
        "host_bios_version": s.get("host_bios_version") or "",
        "host_bios_date": s.get("host_bios_date") or "",
        # Last-observed SNMP auto-detect result — captured from the
        # most recent successful probe's diagnostic. Drives the
        # "Auto-detect last result: <vendors>" hint below the Vendor
        # MIBs checkbox group in the Admin → Hosts editor so operators
        # can see what auto-detect picked before deciding whether to
        # set an explicit override. Empty list when the host has never
        # been probed successfully or no SNMP override is set.
        "host_snmp_active_vendors": list(s.get("host_snmp_active_vendors") or []),
        "host_snmp_active_vendors_source": s.get("host_snmp_active_vendors_source") or "",
        # Network interfaces — already populated by extract_interfaces;
        # added explicitly here so the SNMP path's rx_bytes / tx_bytes /
        # oper_status make it through to the SPA. node-exporter / Beszel
        # / Pulse populate the same field with name + mac + addrs and
        # those merge cleanly via _merge_best (the per-iface dict shape
        # is the same; SNMP just adds the extra rx/tx/oper keys).
        "network_ifaces": list(s.get("network_ifaces") or []),
        # Stale-marker bookkeeping. Populated by
        # apply_host_snapshot_fallback when a provider went down and we
        # filled missing host_* fields from the persisted snapshot.
        # SPA's isStale / isStaleField / staleAge helpers consult these
        # to dim the corresponding bars / fields and surface the
        # "Showing cached data" drawer banner. Empty list / 0 when
        # everything is live so the frontend's reconcile clears the
        # markers cleanly when a provider recovers.
        "_stale_fields": list(s.get("_stale_fields") or []),
        "_stale_ts": float(s.get("_stale_ts") or 0.0),
        # Per-provider sample row counts — populated by
        # `_provider_sample_counts(host_id)` from inside `_merge_one_host`
        # (per-host probe path only; bulk /api/hosts/list path leaves it
        # empty to keep that query cheap). Surfaces under the
        # "Updated Xs ago" subtitle in the host drawer's enabled-agents
        # chip strip — gives the user a snapshot of how much history
        # is available per provider for THIS host. {} when the per-host
        # probe hasn't run yet (cold-load `/api/hosts/list` skeleton).
        "provider_sample_counts": dict(s.get("provider_sample_counts") or {}),
        # Per-provider effective sampler interval (seconds) — third
        # subtitle line in the chip strip. Each value is the post-floor,
        # post-inherit-resolution cadence the sampler actually sleeps
        # for, so the operator sees "Every 5m" not "interval=0 (inherit)".
        "provider_sample_intervals": dict(s.get("provider_sample_intervals") or {}),
        # Permanent-fail tracking. All four fields are non-zero
        # only when the host_metrics_sampler has recorded consecutive
        # failures for this host. `sampling_paused: true` triggers the
        # frontend banner + table icon; the operator clears via POST
        # /api/hosts/{id}/resume-sampling.
        **_failure_state_for_host(h["id"]),
        # Per-provider auto-pause state. Populated only when one
        # or more providers (currently SNMP + Webmin) have a failure-
        # state row keyed `<provider>:<host_id>`. Empty dict for healthy
        # hosts. SPA reads this to render the Paused badge on the
        # provider chip + the Resume button. Operator clears via POST
        # /api/hosts/{id}/provider/{name}/resume.
        "provider_pause_state": _provider_pause_state_for_host(h["id"]),
        # Port-scan provider — latest-scan rollup. Populated by
        # `_merge_one_host` after reading `host_port_scans` for the
        # most recent scan_id; null/empty for hosts with no scan
        # history yet. SPA's host-drawer Port Scan card consumes
        # `detected_ports` for the open-ports chip strip, and
        # `last_port_scan_ts` for the "Scanned X ago" subtitle.
        # Without surfacing these here, the host row sees the fields
        # as undefined → the "No scans yet" message stuck on
        # forever even after a successful scan.
        "detected_ports": s.get("detected_ports") or [],
        "last_port_scan_ts": int(s.get("last_port_scan_ts") or 0),
        # Drift-from-baseline classification. Populated by
        # `_merge_one_host` from logic.host_baseline.host_drift_for_api,
        # keyed by metric (cpu_pct / mem_pct / disk_pct / ping_rtt_ms).
        # Each value carries `indicator` (▲/▼/━), `value` (live), and
        # the cached `median` / `iqr` / `sample_count` / `computed_ts`.
        # Empty dict when no baseline exists for this host yet (<50
        # samples in window OR sampler hasn't run yet) — SPA hides
        # the chip in that case. `_merge_one_host` is the only writer
        # and it always stamps a dict, so no defensive coerce needed.
        "drift": s.get("drift") or {},
        # HTTP / TLS / DNS probe — seventh host-stats provider. Per-host
        # opt-in flag + the resolved URL list are surfaced so the SPA's
        # `providerStates(h)` helper can decide whether to render the
        # http_probe chip + `_hostsConfiguredForProvider('http_probe')`
        # gates the toolbar filter visibility. The probe-result fields
        # (`host_http_*`) are stamped by `populate_host_http_merge` AND
        # surfaced here so the drawer card has the data without a
        # parallel lookup.
        "http_probe_enabled": bool((h.get("http_probe") or {}).get("enabled", False)),
        "http_probe_urls": list((h.get("http_probe") or {}).get("urls") or []),
        # Resolved boolean: does this host have ANY URL source the
        # sampler would actually probe? Mirrors `host_http_sampler`'s
        # URL-resolution chain (http_probe.urls → row.url → row.services[].url)
        # so the SPA's `providerStates(h)` chip-gate can hide the
        # http_probe chip cleanly when the operator enabled the toggle
        # but didn't supply any URLs. Computed backend-side because the
        # API row's `services` field carries the Beszel systemd-rollup
        # OBJECT (`{total, failed, failed_names}`, stamped from
        # `host_services` later in this same row builder), NOT the curated
        # services array (`hosts_config[].services`) — `Array.isArray(h.services)` is
        # always false on the SPA, so the chip-gate can't check the
        # third URL source itself.
        "http_probe_has_targets": (
            bool((h.get("http_probe") or {}).get("urls"))
            or bool((h.get("url") or "").strip())
            or any(
            isinstance(svc, dict) and (svc.get("url") or "").strip()
            for svc in (h.get("services") if isinstance(h.get("services"), list) else [])
        )
        ),
        # Whether ANY curated service chip on this host has probe.enabled —
        # the gate for the Service-probe provider chip. Computed backend-side
        # from the RAW curated services list (h["services"]) because the API
        # row's `services` field above is the Beszel systemd ROLLUP object,
        # not the curated array — so the SPA can't walk it for probe.enabled.
        "service_probe_has_targets": any(
            isinstance(svc, dict) and isinstance(svc.get("probe"), dict)
            and svc.get("probe", {}).get("enabled") is True
            for svc in (h.get("services") if isinstance(h.get("services"), list) else [])
        ),
        # Whether this host has ANY Webmin target — alias key (`webmin_name`)
        # OR direct per-host `webmin_url`. Mirrors `_merge_one_host`'s
        # Webmin URL resolver chain (`webmin_aliases[id]` → per-host
        # `webmin_url` → SKIP) at the curated-row level — the row's
        # `webmin_name` IS the lookup key that resolves through
        # `webmin_aliases` server-side, so a non-empty `webmin_name`
        # implies an alias-routed target without the SPA needing to read
        # the alias map. Pre-fix the SPA's apiGate read `h.webmin_name`
        # only, so a `webmin_url`-configured host's chip silently vanished
        # while the curatedGate (Admin → Hosts editor) still rendered it.
        "webmin_has_target": bool(
            (h.get("webmin_url") or "").strip()
            or (h.get("webmin_name") or "").strip()
        ),
        # Latest sample roll-up. Renders the drawer card + the chip
        # state. None / empty when no sample has landed yet (cold-load
        # before the first tick); the snapshot fallback restores these
        # via `_stale_fields` markers if available.
        "host_http_status_ok": s.get("host_http_status_ok"),
        "host_http_status_code": s.get("host_http_status_code"),
        "host_http_content_match_ok": s.get("host_http_content_match_ok"),
        "host_http_tls_expires_in_days": s.get("host_http_tls_expires_in_days"),
        "host_http_tls_subject": s.get("host_http_tls_subject") or "",
        "host_http_tls_issuer": s.get("host_http_tls_issuer") or "",
        "host_http_dns_resolved": s.get("host_http_dns_resolved"),
        "host_http_dns_error": s.get("host_http_dns_error") or "",
        "host_http_latency_ms": s.get("host_http_latency_ms"),
        "host_http_error": s.get("host_http_error") or "",
        "host_http_url_count_total": int(s.get("host_http_url_count_total") or 0),
        "host_http_url_count_ok": int(s.get("host_http_url_count_ok") or 0),
        "host_http_urls": list(s.get("host_http_urls") or []),
        "host_http_ts": int(s.get("host_http_ts") or 0),
        # Apps feature — curated per-host service chip array surfaced
        # on the API row so the host drawer's Apps sub-tab + chip strip
        # can render them. Each entry carries `name / url / icon /
        # catalog_id / probe / service_idx` plus a stamped `last_probe`
        # block when a service_sampler tick has produced data for that
        # chip. `service_idx` is the position in the source array; the
        # manual probe endpoint (`POST /api/services/{host_id}/{idx}/
        # probe`) keys off it. Empty list = host has no chips pinned.
        "apps": _shape_host_apps(h, _latest_map=_apps_latest_map, _per_port_map=_apps_per_port_map),
    }


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
