"""Per-app module registry — maps catalog template ``slug`` to
the module that handles its custom backend logic.

Route handlers in ``main_pkg/apps_routes.py`` resolve the chip's
catalog template, then call ``module_for_slug(slug)`` to find the
right per-app module. Returns ``None`` for slugs without a custom
module (the chip is generic and uses only the standard probe /
edit / unpin paths).

Adding a new app
----------------
1. Drop the module file under ``logic/apps/<slug>.py`` following
   the ``speedtest_tracker.py`` shape.
2. Add an entry below mapping each slug the module handles to the
   imported module.

The map is intentionally small + explicit (no auto-discovery via
``pkgutil``) so a typo'd slug doesn't silently disable an app's
custom logic.
"""
from __future__ import annotations

from types import ModuleType
from typing import Any, Optional

from logic.coerce import int_or_none

from . import adguardhome
from . import adguardhome_sync
from . import apc
from . import bazarr
from . import ddns_updater
from . import kavita
from . import lidarr
from . import pihole
from . import plex
from . import prowlarr
from . import radarr
from . import readarr
from . import seerr
from . import sonarr
from . import speedtest_tracker

# slug → module. Each module's own ``SLUGS`` tuple lists the
# templates it handles; we explode that here so a single dict
# lookup answers the dispatch question.
_APPS: dict[str, ModuleType] = {}


def _register(module: ModuleType) -> None:
    """Walk one module's ``SLUGS`` tuple and stamp each entry
    into the dispatch dict."""
    slugs = getattr(module, "SLUGS", ())
    for slug in slugs:
        s = str(slug or "").strip().lower()
        if s:
            _APPS[s] = module


_register(adguardhome)
_register(adguardhome_sync)
_register(apc)
_register(bazarr)
_register(ddns_updater)
_register(kavita)
_register(lidarr)
_register(pihole)
_register(plex)
_register(prowlarr)
_register(radarr)
_register(readarr)
_register(seerr)
_register(sonarr)
_register(speedtest_tracker)


def module_for_slug(slug: str) -> Optional[ModuleType]:
    """Return the per-app module for a catalog template slug, or
    ``None`` when no custom module is registered (= generic chip).
    """
    if not slug:
        return None
    return _APPS.get(str(slug).strip().lower())


def all_slugs() -> tuple[str, ...]:
    """All registered slugs — used by the SPA's
    ``appsTemplateRequiresApiKey`` / ``appsTemplateSupportsExtras``
    surrogates when the SPA wants to ask the backend "which
    templates have custom logic?" without re-implementing the
    dispatch in JS. Currently consumed via ``/api/me``'s
    ``client_config`` block (future enhancement)."""
    return tuple(sorted(_APPS.keys()))


# ---------------------------------------------------------------------------
# App SKILLS — the extensible "AI skill" surface. A per-app module exposes a
# ``SKILLS`` tuple of dicts ``{id, name, ai_phrases?, destructive?}`` and an
# ``async run_skill(skill_id, host_row, chip, *, host_id, service_idx) -> dict``
# coroutine. Each skill is BOTH an app-drawer button AND an AI / Telegram-AI
# action the model can invoke — but ONLY when the app's extras are enabled and
# its api_key is set (the route + the prompt-injection layer enforce that gate;
# the registry just enumerates what a slug CAN do). This is the first of a
# per-app skill pattern: enabling a new app with extra functionality adds its
# skills here automatically via its module's SKILLS tuple — no registry edit.
# ---------------------------------------------------------------------------
def skills_for_slug(slug: str) -> tuple[dict, ...]:
    """Return the validated ``SKILLS`` a per-app module declares for a slug,
    or ``()`` when the module declares none / isn't registered. Each entry is
    a dict carrying at least an ``id``."""
    mod = module_for_slug(slug)
    if mod is None:
        return ()
    skills = getattr(mod, "SKILLS", ())
    return tuple(s for s in skills if isinstance(s, dict) and s.get("id"))


def skill_is_destructive(slug: str, skill_id: str) -> bool:
    """True when the named skill declares ``destructive: True`` in its module
    SKILLS. The single source of truth every dispatch surface (web route /
    Telegram slash / Telegram-AI / inline-button) consults to decide whether a
    confirm gate applies. False for an unknown slug / skill (fail-open is safe —
    a non-existent skill can't run anyway; the dispatcher rejects it first)."""
    for s in skills_for_slug(slug):
        if s.get("id") == skill_id:
            return bool(s.get("destructive"))
    return False


def all_app_skills() -> dict[str, list[dict]]:
    """Map every registered slug that declares skills to its skill list —
    surfaced on ``/api/me``'s ``client_config.app_skills`` so the SPA (drawer
    buttons) + the AI context (available-skill prompt injection) can enumerate
    skills without re-implementing the per-module dispatch in JS."""
    out: dict[str, list[dict]] = {}
    for slug in _APPS:
        skills = skills_for_slug(slug)
        if skills:
            out[slug] = [dict(s) for s in skills]
    return out


def _chip_slug(chip: dict, cat_by_id: Optional[dict] = None) -> str:
    """Resolve a chip's catalog slug from any shape a persisted chip can
    carry: an explicit ``catalog_slug`` string, an embedded ``catalog``
    block, or -- the dominant case for pinned chips -- a numeric
    ``catalog_id`` FK that has to be looked up against the catalog table.

    Pinned chips persist ONLY ``catalog_id`` (the slug is derived at
    shape time by `_shape_host_apps` / `list_apps`), so WITHOUT the id
    fallback every catalog-linked app is invisible to the per-app skill
    dispatch AND the AI / Telegram-AI ``app_skills`` context -- the model
    then reports the app as "not configured" even when its api_key is set
    and its extras render in the UI. Pass ``cat_by_id`` (an id->row map)
    to avoid a per-chip DB read inside a loop; omit it for a single-chip
    lookup. Returns "" when nothing resolves."""
    if not isinstance(chip, dict):
        return ""
    _cat = chip.get("catalog")
    cat = _cat if isinstance(_cat, dict) else {}
    slug = str(chip.get("catalog_slug") or cat.get("slug") or "").strip().lower()
    if slug:
        return slug
    cid_int = int_or_none(chip.get("catalog_id"))
    if cid_int is None:
        return ""
    if cat_by_id is not None:
        row = cat_by_id.get(cid_int)
    else:
        from logic.service_catalog import get_catalog_by_id  # noqa: PLC0415
        row = get_catalog_by_id(cid_int)
    return str((row or {}).get("slug") or "").strip().lower()


def resolve_chip(host_id: str, service_idx: int):
    """Resolve ``(host_row, chip, slug)`` for a pinned app chip from
    hosts_config — the server-side counterpart to the route's
    ``_resolve_chip_app_module`` (no Request needed; used by the Telegram-AI
    skill dispatch). Returns ``(None, None, "")`` when not found. Never raises.
    """
    # defensive "never raises" boundary: a bad hosts_config blob / DB read must
    # yield "not found", never propagate.
    # noinspection PyBroadException
    try:
        import json as _json  # noqa: PLC0415
        from logic.db import get_setting  # noqa: PLC0415
        from logic.settings_keys import Settings  # noqa: PLC0415
        raw = get_setting(Settings.HOSTS_CONFIG) or ""
        hosts = _json.loads(raw) if raw.strip() else []
    except Exception:  # noqa: BLE001
        return None, None, ""
    if not isinstance(hosts, list):
        return None, None, ""
    hid = str(host_id or "").strip()
    for h in hosts:
        if isinstance(h, dict) and str(h.get("id") or "").strip() == hid:
            svcs = h.get("services") or []
            if (isinstance(svcs, list) and isinstance(service_idx, int)
                and 0 <= service_idx < len(svcs) and isinstance(svcs[service_idx], dict)):
                chip = svcs[service_idx]
                return h, chip, _chip_slug(chip)
            return h, None, ""
    return None, None, ""


def _load_hosts_and_catmap() -> "tuple[list, dict[int, dict[str, Any]]]":
    """Load ``hosts_config`` + the catalog id->row map ONCE. Shared by
    ``instances_for_slug`` + ``available_app_skills_context`` so the
    hosts-config decode + catalog map build live in one place. Returns
    ``([], {})`` on any failure (defensive — callers stay safe / never
    raise). No upstream calls."""
    # noinspection PyBroadException
    try:
        import json as _json  # noqa: PLC0415
        from logic.db import get_setting  # noqa: PLC0415
        from logic.settings_keys import Settings  # noqa: PLC0415
        raw = get_setting(Settings.HOSTS_CONFIG) or ""
        hosts = _json.loads(raw) if raw.strip() else []
    except Exception:  # noqa: BLE001
        return [], {}
    if not isinstance(hosts, list):
        return [], {}
    from logic.service_catalog import list_catalog  # noqa: PLC0415
    cat_by_id: dict[int, dict[str, Any]] = {}
    for r in list_catalog():
        _rid = int_or_none(r.get("id"))
        if _rid is not None:
            cat_by_id[_rid] = r
    return hosts, cat_by_id


def instances_for_slug(slug: str) -> list:
    """Enumerate every pinned chip whose catalog slug matches ``slug`` as
    ``[(host_id, service_idx, host_row, chip)]`` — the server-side fleet
    enumerator for apps whose skills act across ALL their instances (e.g.
    AdGuard's fleet enable/disable). Never raises (returns [] on any
    failure). No upstream calls — pure hosts_config walk."""
    want = str(slug or "").strip().lower()
    if not want:
        return []
    out: list = []
    hosts, cat_by_id = _load_hosts_and_catmap()
    for h in hosts:
        if not isinstance(h, dict):
            continue
        host_id = str(h.get("id") or "").strip()
        services = h.get("services") or []
        if not isinstance(services, list):
            continue
        for idx, chip in enumerate(services):
            if not isinstance(chip, dict):
                continue
            if _chip_slug(chip, cat_by_id) == want:
                out.append((host_id, idx, h, chip))
    return out


def available_app_skills_context(datetime_format: Optional[str] = None) -> list:
    """Build the AI / Telegram-AI ``app_skills`` context list: every pinned
    app chip whose app declares SKILLS AND (when the app requires it) has its
    api_key set. Each entry is
    ``{host_id, host, service_idx, slug, app, skills:[{id,name}], last?}`` so
    the model can ONLY invoke skills that are actually runnable. NO upstream
    calls — ``last`` is a cache-only peek. Never raises (returns [] on any
    failure so context assembly is safe).

    When ``datetime_format`` is supplied (the requesting operator's
    ``ui_prefs.datetime_format``), each ``last`` carrying a ``ts`` gains a
    ``ts_display`` rendered in that format + the operator's scheduler
    timezone — so the AI reply shows the timestamp the way the operator
    set it under Settings → Profile → Formats."""
    out: list = []
    # Shared hosts_config decode + catalog id->row map (defensive — returns
    # [], {} on any failure so context assembly stays safe). The map makes
    # per-chip slug + display-name resolution O(1) inside the loop.
    hosts, cat_by_id = _load_hosts_and_catmap()
    for h in hosts:
        if not isinstance(h, dict):
            continue
        host_id = str(h.get("id") or "").strip()
        host_label = str(h.get("label") or host_id)
        services = h.get("services") or []
        if not isinstance(services, list):
            continue
        for idx, chip in enumerate(services):
            if not isinstance(chip, dict):
                continue
            slug = _chip_slug(chip, cat_by_id)
            if not slug:
                continue
            skills = skills_for_slug(slug)
            if not skills:
                continue
            mod = module_for_slug(slug)
            _req = getattr(mod, "requires_api_key", None)
            if callable(_req) and _req() and not str(chip.get("api_key") or "").strip():
                continue  # gated: app needs an api_key and none is set
            # Fleet-ness: a skill aggregates across EVERY instance (run_skill
            # ignores the chip) when its own `fleet` flag is set OR the module
            # declares `FLEET_SKILLS = True` (e.g. AdGuard). Surfaced so the
            # Telegram slash command + /help can run it host-less.
            _mod_fleet = bool(getattr(mod, "FLEET_SKILLS", False))
            # App display name: chip override -> catalog template name -> slug.
            _cidi = int_or_none(chip.get("catalog_id"))
            _row = cat_by_id.get(_cidi) if _cidi is not None else None
            _rname = _row.get("name") if isinstance(_row, dict) else None
            app_name = str(chip.get("name") or _rname or slug)
            entry: dict[str, Any] = {
                "host_id": host_id,
                "host": host_label,
                "service_idx": idx,
                "slug": slug,
                "app": app_name,
                "skills": [{"id": s.get("id"), "name": s.get("name"),
                            "fleet": bool(s.get("fleet")) or _mod_fleet,
                            # ai_phrases = comma-separated example phrasings the
                            # model matches the operator's request against to
                            # pick the right skill_id (e.g. 'pause blocking for
                            # 10 min' → adguard_disable_10m). Rendered by
                            # ai_extras.build_palette_user_prompt.
                            "ai_phrases": s.get("ai_phrases") or "",
                            # arg / arg_hint = this skill takes a free-form
                            # argument the model must supply in ACTION_DATA's
                            # `arg` field (e.g. Seerr request-a-movie title).
                            "arg": bool(s.get("arg")),
                            "arg_hint": s.get("arg_hint") or ""}
                           for s in skills],
            }
            last = peek_skill_data(slug, host_id, idx)
            if last:
                # Stamp a human ts_display in the operator's chosen format so
                # the AI reply renders the timestamp consistently with the
                # rest of the UI. Modules differ on the timestamp field:
                # Speedtest emits `ts` (ISO from the upstream result), while the
                # DNS-blocker modules (AdGuard / Pi-hole) emit `fetched_at`
                # (epoch int = when the cache was filled). Fall back to
                # `fetched_at` so every module gets a ts_display
                # (format_user_datetime accepts epoch / ISO / datetime alike).
                _ts_val = last.get("ts") or last.get("fetched_at") if isinstance(last, dict) else None
                if datetime_format and _ts_val:
                    # noinspection PyBroadException
                    try:
                        from logic.datetime_fmt import format_user_datetime  # noqa: PLC0415
                        disp = format_user_datetime(_ts_val, datetime_format)
                        if disp:
                            last["ts_display"] = disp
                    except Exception:  # noqa: BLE001
                        pass
                entry["last"] = last
            out.append(entry)
    return out


def peek_skill_data(slug: str, host_id: str, service_idx: int) -> Optional[dict]:
    """Best-effort, NO-upstream-call peek at a per-app module's latest cached
    data for the AI context's ``app_skills[].last`` field. Returns ``None``
    when the module has no ``peek_latest`` hook or nothing is cached. Never
    raises (a peek failure must not break context assembly)."""
    mod = module_for_slug(slug)
    if mod is None or not hasattr(mod, "peek_latest"):
        return None
    # per-app peek_latest is arbitrary module code; a peek failure must never
    # break AI-context assembly.
    # noinspection PyBroadException
    try:
        return mod.peek_latest(host_id, service_idx)
    except Exception:  # noqa: BLE001
        return None


async def run_app_skill(slug: str, skill_id: str, host_row: dict, chip: dict,
                        **kwargs) -> dict:
    """Dispatch one skill to its per-app module's ``run_skill`` coroutine.
    Raises ``ValueError`` when the slug has no module / no ``run_skill`` / an
    unknown ``skill_id`` so the caller can map it to an HTTP 400 / 404."""
    _hid = kwargs.get("host_id")
    _sidx = kwargs.get("service_idx")
    mod = module_for_slug(slug)
    if mod is None or not hasattr(mod, "run_skill"):
        print(f"[app_skill] warning: run skipped — no run_skill module for "
              f"slug={slug!r} (skill={skill_id!r} host={_hid} svc_idx={_sidx})")
        raise ValueError(f"no skills for app slug: {slug!r}")
    valid = {s.get("id") for s in skills_for_slug(slug)}
    if skill_id not in valid:
        print(f"[app_skill] warning: run skipped — unknown skill {skill_id!r} for "
              f"slug={slug!r} (valid={sorted(str(v) for v in valid if v)} host={_hid} svc_idx={_sidx})")
        raise ValueError(f"unknown skill {skill_id!r} for app {slug!r}")
    print(f"[app_skill] INFO run start slug={slug!r} skill={skill_id!r} "
          f"host={_hid} svc_idx={_sidx}")
    result = await mod.run_skill(skill_id, host_row, chip, **kwargs)
    _ok = isinstance(result, dict) and result.get("ok")
    _detail = (result or {}).get("detail") if isinstance(result, dict) else None
    if _ok:
        print(f"[app_skill] INFO run done slug={slug!r} skill={skill_id!r} "
              f"host={_hid} -> ok ({_detail})")
    else:
        print(f"[app_skill] warning: run did not succeed slug={slug!r} "
              f"skill={skill_id!r} host={_hid} -> {_detail}")
    return result
