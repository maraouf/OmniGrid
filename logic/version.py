"""App version reading.

Two-layer model (#371):

* **PATCH** — CI-managed. Lives in ``/app/VERSION.txt`` on the server;
  the Forgejo Actions pipeline auto-increments it on every successful
  deploy. Rsync deliberately excludes ``VERSION.txt`` so deploys never
  overwrite the bumped value.
* **MAJOR.MINOR** — operator-managed via the Admin → Version UI. Stored
  in the settings table (``version_major`` / ``version_minor``). Layered
  on top of the file's patch number when both are set; falls back to
  the raw file content for back-compat when the DB override is absent.

So the rendered string is ``f"{db.major}.{db.minor}.{file.patch}"`` when
the DB override exists, else the file's content as-is.

For local dev, the repo-root ``VERSION.txt`` is used. Missing file →
``"0.0.0-dev"`` as a visible signal.

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


def _db_version_override() -> Optional[Tuple[int, int]]:
    """Read the operator-set MAJOR/MINOR from the settings table.

    Returns ``(major, minor)`` when both keys are present and parseable
    as ints, or ``None`` to fall back to the raw VERSION.txt content.
    Lazy-imports ``logic.db`` to avoid a circular at module-import time
    (``main.py`` imports ``APP_VERSION`` very early, before db setup).
    Any error → ``None`` (safe fallback).
    """
    try:
        from logic.db import get_setting
        major_s = get_setting("version_major", "")
        minor_s = get_setting("version_minor", "")
        if not major_s or not minor_s:
            return None
        return int(major_s), int(minor_s)
    except Exception:
        return None


def read_version() -> str:
    """Public version-string accessor. Called once at import via
    APP_VERSION + on demand by the API routes that need a fresh value
    (the DB override can change at runtime via the Admin → Version
    page; APP_VERSION is captured at startup, so that constant is a
    snapshot, not a live read).
    """
    raw = _read_version_file()
    override = _db_version_override()
    if override is None:
        return raw
    _, _, patch = _split_version(raw)
    major, minor = override
    return f"{major}.{minor}.{patch}"


APP_VERSION = read_version()
