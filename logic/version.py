"""App version reading.

Components are layered: each of MAJOR / MINOR / PATCH can be
operator-overridden via the Admin → Version UI (settings keys
``version_major`` / ``version_minor`` / ``version_patch``). Anything
not overridden falls back to ``/app/VERSION.txt`` on the server (which
the deployment pipeline manages — it bumps PATCH on every successful
deploy and rsync excludes the file so deploys can't overwrite it).

So the rendered string is built component-by-component: each piece is
the DB override when present, otherwise the matching component from
the file. For local dev, the repo-root ``VERSION.txt`` is used.
Missing file → ``"0.0.0-dev"`` as a visible signal.

Rendered in the UI footer and returned by GET /api/version /
GET /api/healthz / GET /api/admin/version.
"""
import os
from typing import Optional, Tuple


def _read_version_file() -> str:
    """Read the raw ``VERSION.txt`` content (CI-managed PATCH counter).

    Tries the dev-side repo-root file first, falls back to the prod
    bind-mounted path. Returns ``"0.0.0-dev"`` when nothing's readable.
    """
    candidates = (
        # Dev: repo-root VERSION.txt, relative to the project (logic/
        # version.py sits inside logic/ which sits at the repo root).
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "VERSION.txt"),
        # Prod: bind-mounted file the CI pipeline writes on every deploy.
        "/app/VERSION.txt",
    )
    for p in candidates:
        try:
            with open(p, "r", encoding="utf-8") as f:
                v = f.read().strip().splitlines()[0].strip()
                if v:
                    return v
        except (OSError, IndexError):
            continue
    return "0.0.0-dev"


def _split_version(raw: str) -> Tuple[int, int, int]:
    """Best-effort parse of a SemVer-shaped string into (major, minor, patch).

    Tolerates the ``"0.0.0-dev"`` sentinel (returns 0/0/0) and any short
    forms (single ``"5"`` → 5/0/0; ``"1.2"`` → 1/2/0).
    """
    parts = (raw or "").split("-", 1)[0].split(".")
    nums = []
    for p in parts:
        try:
            nums.append(int(p))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def _db_version_overrides() -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Read per-component operator overrides from the settings table.

    Returns ``(major, minor, patch)`` where each entry is the parsed
    int when set, or ``None`` to fall back to the file value for that
    component. Lazy-imports ``logic.db`` to avoid a circular at module-
    import time (``main.py`` imports ``APP_VERSION`` very early, before
    db setup). Any error → ``(None, None, None)`` (safe fallback).
    """
    try:
        from logic.db import get_setting
        def _read(key: str) -> Optional[int]:
            v = get_setting(key, "")
            if not v:
                return None
            try:
                return int(v)
            except (ValueError, TypeError):
                return None
        return _read("version_major"), _read("version_minor"), _read("version_patch")
    except Exception:
        return None, None, None


def read_version() -> str:
    """Public version-string accessor. Called once at import via
    APP_VERSION + on demand by the API routes that need a fresh value
    (the DB override can change at runtime via the Admin → Version
    page; APP_VERSION is captured at startup, so that constant is a
    snapshot, not a live read).
    """
    raw = _read_version_file()
    o_major, o_minor, o_patch = _db_version_overrides()
    if o_major is None and o_minor is None and o_patch is None:
        return raw
    f_major, f_minor, f_patch = _split_version(raw)
    major = o_major if o_major is not None else f_major
    minor = o_minor if o_minor is not None else f_minor
    patch = o_patch if o_patch is not None else f_patch
    return f"{major}.{minor}.{patch}"


APP_VERSION = read_version()
