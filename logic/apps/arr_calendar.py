"""Shared *arr release-calendar aggregator.

Fans out every CONFIGURED Radarr / Sonarr / Lidarr / Readarr instance's
``calendar_items`` over a ``[start_iso, end_iso]`` window and merges them into
one normalised list (each row carries its ``host_id`` / ``service_idx`` so the
caller can route a poster through the per-app image proxy). The per-app
``calendar_items`` returns the rich detail every surface needs — title,
subtitle (episode code / album), type, date, air time, synopsis (``overview``),
runtime, deep links.

Consumed by BOTH the Apps custom-dashboard ``arr_calendar`` widget route
(``main_pkg/apps_routes.py:api_apps_arr_calendar``) AND the AI palette's
``upcoming_releases`` tool (``logic/ai_extras.py``), so the fan-out lives in ONE
place. Dependency-free leaf (imports only ``asyncio`` + the registry lazily) so
importing it can't create a cycle.
"""
from __future__ import annotations

import asyncio

# The *arr services that expose a release calendar (Prowlarr has none — it's an
# indexer manager). Each module declares an async ``calendar_items``.
ARR_CAL_SLUGS: tuple[str, ...] = ("radarr", "sonarr", "lidarr", "readarr")


async def collect_calendar(start_iso: str, end_iso: str) -> dict:
    """Aggregate upcoming-release rows across every configured *arr instance
    for the ``[start_iso, end_iso]`` window (ISO-8601 ``YYYY-MM-DDTHH:MM:SSZ``).

    Returns ``{configured, services, items, errors}``:
      - ``configured`` — True when at least one *arr instance (a pinned chip with
        an api_key) exposes a calendar.
      - ``services`` — sorted list of slugs that actually contributed rows.
      - ``items`` — merged normalised rows (each with ``host_id`` /
        ``service_idx`` stamped on).
      - ``errors`` — ``{"<slug>:<host>:<idx>": "<reason>"}`` for any instance
        that failed (one bad instance never sinks the rest).

    Never raises (a per-instance failure is captured into ``errors``)."""
    from logic.apps import registry as _reg  # noqa: PLC0415
    targets = []
    for slug in ARR_CAL_SLUGS:
        mod = _reg.module_for_slug(slug)
        if mod is None or not hasattr(mod, "calendar_items"):
            continue
        for host_id, sidx, host_row, chip in _reg.instances_for_slug(slug):
            if isinstance(chip, dict) and str(chip.get("api_key") or "").strip():
                targets.append((slug, host_id, sidx, mod, host_row, chip))

    async def _one(target):
        t_slug, t_hid, t_sidx, t_mod, t_row, t_chip = target
        try:
            t_rows = await t_mod.calendar_items(t_row, t_chip, start=start_iso, end=end_iso)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as e:  # noqa: BLE001 — one bad instance must not sink the rest
            return t_slug, t_hid, t_sidx, [], f"{type(e).__name__}: {e}"
        return t_slug, t_hid, t_sidx, t_rows, None

    results = await asyncio.gather(*[_one(t) for t in targets])
    items: list[dict] = []
    services_active: set = set()
    errors: dict[str, str] = {}
    for r_slug, r_hid, r_sidx, r_rows, r_err in results:
        if r_err:
            errors[f"{r_slug}:{r_hid}:{r_sidx}"] = r_err
            continue
        if r_rows:
            services_active.add(r_slug)
        for r in r_rows:
            if not isinstance(r, dict):
                continue
            r2 = dict(r)
            r2["host_id"] = r_hid
            r2["service_idx"] = r_sidx
            items.append(r2)
    return {
        "configured": bool(targets),
        "services": sorted(services_active),
        "items": items,
        "errors": errors,
    }
