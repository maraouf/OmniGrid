"""Emby (media server) per-app module.

Thin binder over the shared Emby / Jellyfin base (``logic/apps/_emby.py``) —
Jellyfin is a fork of Emby, so the two share ~95% of the REST API. This module
supplies only the Emby-specific config (brand label + the ``emby`` auth scheme:
the native ``X-Emby-Token: <key>`` header) + its own data cache + SKILLS tuple;
every byte of fetch / skill logic lives in ``_emby.py`` (the same
de-duplication discipline the ``*arr`` family uses via ``_servarr``).

Auth: a server-issued API key (Settings → Advanced → API Keys), stored in the
chip's ``api_key`` field. Single-instance app (NOT fleet).

Sibling binder ``jellyfin.py`` is intentionally near-identical (both delegate
every call to ``_emby`` with only their own ``Config``); the per-app registry
pattern requires one module per app, so the parallel-binder duplication is
sanctioned (the ``# noinspection DuplicatedCode`` before the delegators
suppresses the IDE's cross-file clone warning, matching the ``*arr`` binders).
"""
from __future__ import annotations

from typing import Optional

from logic.apps import _emby

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("emby",)

# Emby binding config — native X-Emby-Token auth scheme.
_CFG = _emby.Config(brand="Emby", scheme="emby", log_tag="emby", slug="emby")

# Per-(host_id, service_idx) data cache for the expanded card.
_data_cache: dict[str, tuple[float, dict]] = {}

# The shared 5-skill set, branded for Emby.
SKILLS: tuple[dict, ...] = _emby.build_skills("emby", "Emby")


# noinspection DuplicatedCode
def requires_api_key() -> bool:
    """Emby authenticates via an API key (Settings → Advanced → API Keys); the
    editor MUST render the key input (stored in the chip's api_key) + Test."""
    return True


def image_proxy_url(host_row: dict, chip: dict, path: str) -> "tuple[str, dict]":
    """Per-app image-proxy hook (poster / avatar) — shared base, Emby auth."""
    return _emby.image_proxy_url(host_row, chip, path, scheme=_CFG.scheme)


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe ``/System/Info`` with the supplied API key."""
    return await _emby.test_credential(host_row, chip, candidate_key, cfg=_CFG)


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch the library summary for the card (shared base)."""
    return await _emby.fetch_data(host_row, chip, host_id=host_id,
                                  service_idx=service_idx, force=force,
                                  cfg=_CFG, cache=_data_cache)


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek for the AI context's ``app_skills[].last``."""
    return _emby.peek_latest(host_id, service_idx, cache=_data_cache)


async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None,
                    arg: Optional[str] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS (shared base)."""
    return await _emby.run_skill(skill_id, host_row, chip, host_id=host_id,
                                 service_idx=service_idx, arg=arg,
                                 cfg=_CFG, cache=_data_cache)
