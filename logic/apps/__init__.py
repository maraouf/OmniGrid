"""Per-app modules — one file per catalog template that needs
custom backend logic (auth-required data fetch, test-credential
probe, expanded-card data shape, etc.).

Lookup convention
-----------------
Each module exposes a small public surface keyed off the catalog
template's ``slug``. The dispatch table in ``logic.apps.registry``
maps slug → module so route handlers stay thin (one dispatch +
no per-slug if-chain).

Adding a new per-app module
---------------------------
1. Create ``logic/apps/<slug>.py``. Use ``logic/apps/speedtest_tracker.py``
   as the reference shape.
2. Implement the public coroutines / helpers the app needs (typical
   set: ``async def test_credential(host_row, chip, candidate_key)
   -> dict`` and ``async def fetch_data(host_row, chip) -> dict``).
3. Register in ``logic/apps/registry.py``'s ``APPS`` dict mapping
   the template's slug (or alias tuple) to the module.

The endpoints in ``main_pkg/apps_routes.py`` resolve the chip's
catalog template, look up the per-app module via the registry, and
delegate. They stay app-agnostic — adding a new app does NOT touch
``apps_routes.py``.
"""
