"""App version reading.

Source of truth at runtime is /app/VERSION.txt — the Forgejo Actions
pipeline writes it per-deploy (MAJOR.MINOR preserved, PATCH set to the
workflow run_number). For local dev, the repo-root VERSION.txt is used
instead. Missing file → ``"0.0.0-dev"`` as a visible signal.

Rendered in the UI footer and returned by GET /api/version / /api/healthz.
"""
import os


def read_version() -> str:
    """Read the app version string. Called once at import via APP_VERSION."""
    candidates = (
        # Dev: repo-root VERSION.txt, relative to the project (three dirs
        # up from this file's location because logic/version.py sits inside
        # logic/ which sits at the repo root).
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "VERSION.txt"),
        # Prod: the bind-mounted file the CI pipeline writes on every deploy.
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


APP_VERSION = read_version()
