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
from typing import Optional

from . import apc
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


_register(apc)
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
