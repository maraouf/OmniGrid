"""App version reading.

The runtime source of truth is ``/app/VERSION.txt`` baked into the
image at build time via the Dockerfile's ``ARG VERSION``. The
deployment pipeline overrides VERSION on every successful build
(deploy.yml computes MAX(live /api/version, repo VERSION.txt, highest
local image tag) + 1 PATCH). For local dev, the repo-root
``VERSION.txt`` is used as a fallback. Missing file → ``"0.0.0-dev"``
as a visible signal.

Pre-2026-04-30 this module also exposed ``write_version`` for the
Admin → Version page. Both were removed alongside the deploy migration
to image-build : the per-file bind mount that made writes
durable no longer exists, so any container-side write would land in
the overlay layer and disappear on the next ``service update --force``.
Operators now seed MAJOR/MINOR by editing repo-root ``VERSION.txt``,
committing, and pushing — deploy.yml's source-B resolver picks it up.

Rendered in the UI footer and returned by GET /api/version /
GET /api/healthz.
"""
import os
from typing import Tuple


def _candidate_paths() -> Tuple[str, ...]:
    """Search order for VERSION.txt — dev-side first, then prod."""
    # ``__file__`` is typed as ``str | bytes | LiteralString | Any`` in
    # some stub revisions, which makes ``os.path.dirname`` unhappy. Cast
    # explicitly to ``str`` so the join chain stays type-stable.
    _here = str(__file__)
    return (
        # Dev: repo-root VERSION.txt, relative to the project (logic/
        # version.py sits inside logic/ which sits at the repo root).
        os.path.join(os.path.dirname(os.path.dirname(_here)), "VERSION.txt"),
        # Prod: file baked into the image at build time via Dockerfile's
        # ARG VERSION + `RUN echo "$VERSION" > /app/VERSION.txt`.
        "/app/VERSION.txt",
    )


def _read_version_file() -> str:
    """Read the raw ``VERSION.txt`` content.

    Tries the dev-side repo-root file first, falls back to the
    image-baked path. Returns ``"0.0.0-dev"`` when nothing's readable.
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


def read_version() -> str:
    """Public version-string accessor."""
    return _read_version_file()


APP_VERSION = read_version()
