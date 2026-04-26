"""App version reading + writing.

The single source of truth is ``/app/VERSION.txt`` on the server. The
deployment pipeline bumps PATCH on every successful deploy. The
Admin → Version UI also writes the same file directly when the
operator wants to reset PATCH after cutting a MINOR release. Both
writers (CI + UI) target the same path; whoever writes last wins.

For local dev, the repo-root ``VERSION.txt`` is used. Missing file →
``"0.0.0-dev"`` as a visible signal.

Rendered in the UI footer and returned by GET /api/version /
GET /api/healthz / GET /api/admin/version.
"""
import os
from typing import Tuple


def _candidate_paths() -> Tuple[str, ...]:
    """Search order for VERSION.txt — dev-side first, then prod."""
    return (
        # Dev: repo-root VERSION.txt, relative to the project (logic/
        # version.py sits inside logic/ which sits at the repo root).
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "VERSION.txt"),
        # Prod: bind-mounted file the CI pipeline writes on every deploy.
        "/app/VERSION.txt",
    )


def _read_version_file() -> str:
    """Read the raw ``VERSION.txt`` content.

    Tries the dev-side repo-root file first, falls back to the prod
    bind-mounted path. Returns ``"0.0.0-dev"`` when nothing's readable.
    """
    for p in _candidate_paths():
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


def write_version(major: int, minor: int, patch: int) -> str:
    """Overwrite the first writable VERSION.txt with ``M.N.P``.

    Open-and-truncate (``open(path, "w")``) keeps the existing inode
    and ownership intact, so the deploy pipeline's pi-user SSH writes
    can still update the file after a container-side write. Returns
    the value written. Raises ``OSError`` (the last seen error) when
    none of the candidate paths is writable — a missing writable
    bind-mount in compose surfaces here as ``Read-only file system``
    or ``Permission denied``, which the API caller propagates to the
    UI as a save-failed toast.
    """
    raw = f"{int(major)}.{int(minor)}.{int(patch)}"
    last_err: Exception = OSError("no candidate paths")
    for p in _candidate_paths():
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(raw + "\n")
            return raw
        except OSError as e:
            last_err = e
            continue
    raise last_err


def read_version() -> str:
    """Public version-string accessor."""
    return _read_version_file()


APP_VERSION = read_version()
