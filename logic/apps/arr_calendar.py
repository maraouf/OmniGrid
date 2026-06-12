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

# Synonyms the operator's natural language uses for each *arr media kind →
# the per-app calendar row's ``type`` field (movie / episode / album / book).
# Empty string = no type filter (return everything upcoming). Shared by the AI
# palette's ``upcoming_releases`` tool AND the Telegram ``/upcoming`` command.
MEDIA_TYPE_MAP: dict[str, str] = {
    "movie": "movie", "movies": "movie", "film": "movie", "films": "movie",
    "series": "episode", "show": "episode", "shows": "episode", "tv": "episode",
    "episode": "episode", "episodes": "episode",
    "album": "album", "albums": "album", "music": "album", "song": "album",
    "songs": "album", "track": "album", "tracks": "album",
    "book": "book", "books": "book", "audiobook": "book", "audiobooks": "book",
}


def normalize_media_type(word) -> str:
    """Map a free-text media-kind word to the canonical per-app ``type`` value
    (``movie`` / ``episode`` / ``album`` / ``book``), or ``""`` for no filter."""
    return MEDIA_TYPE_MAP.get(str(word or "").strip().lower(), "")


async def upcoming_items(*, days: int = 14, media_type: str = "",
                         title: str = "", limit: int = 40) -> dict:
    """Aggregate + filter + sort UPCOMING releases across every configured *arr
    instance for the next ``days`` (clamped 1..90).

    Returns ``{configured, services, window, count, items, errors}`` where each
    item is a normalised ``{date, time, airdate_utc, title, subtitle, type,
    service, overview, runtime_min}`` row (soonest-first, capped at ``limit``).
    ``airdate_utc`` is the full UTC ISO datetime for rows with a real air time
    (Sonarr episodes), or ``""`` for date-only releases (movies / albums /
    books) — a tz-aware caller renders the time in the operator's timezone.
    ``media_type`` filters to one kind (accepts a synonym via
    ``normalize_media_type``); ``title`` filters to a title/subtitle substring.
    Shared by the AI ``upcoming_releases`` tool + the Telegram ``/upcoming``
    command. Never raises — a fetch failure surfaces in the result's ``errors``."""
    from datetime import datetime, timedelta, timezone  # noqa: PLC0415
    try:
        win = max(1, min(90, int(days or 14)))
    except (TypeError, ValueError):
        win = 14
    want = normalize_media_type(media_type)
    title_q = str(title or "").strip().lower()
    now = datetime.now(timezone.utc)
    start_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = (now + timedelta(days=win)).strftime("%Y-%m-%dT23:59:59Z")
    agg = await collect_calendar(start_iso, end_iso)
    out_items: list[dict] = []
    for it in (agg.get("items") or []):
        if not isinstance(it, dict):
            continue
        if want and str(it.get("type") or "").lower() != want:
            continue
        t_title = str(it.get("title") or "")
        subtitle = str(it.get("subtitle") or "")
        if title_q and title_q not in (t_title + " " + subtitle).lower():
            continue
        try:
            runtime_min = max(0, int(it.get("runtime") or 0))
        except (TypeError, ValueError):
            runtime_min = 0
        out_items.append({
            "date": str(it.get("date") or ""),
            "time": str(it.get("time") or ""),
            # Full UTC ISO datetime when the row has a real air time (Sonarr
            # episodes); "" for date-only releases (movies / albums / books). A
            # tz-aware consumer renders the broadcast time in the local tz.
            "airdate_utc": str(it.get("airdate_utc") or ""),
            "title": t_title,
            "subtitle": subtitle,
            "type": str(it.get("type") or ""),
            "service": str(it.get("service_slug") or ""),
            "overview": str(it.get("overview") or ""),
            "runtime_min": runtime_min,
        })
    out_items.sort(key=lambda r: (r.get("date") or "", r.get("time") or ""))
    out_items = out_items[:max(1, int(limit or 40))]
    return {
        "configured": bool(agg.get("configured")),
        "services": agg.get("services") or [],
        "window": {"start": start_iso[:10], "end": end_iso[:10], "days": win},
        "filters": {"media_type": want, "title": title_q},
        "count": len(out_items),
        "items": out_items,
        "errors": agg.get("errors") or {},
    }


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
