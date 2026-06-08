"""Canonical external URLs + hostnames used across OmniGrid.

Single home for third-party base URLs / hostnames that would otherwise be
repeated as string literals across modules (GitHub, TMDB, Plex, …). Same intent
as ``logic.settings_keys.Settings`` / ``logic.env_keys.EnvKey`` /
``logic.tuning.Tunable``: one declaration, one place to audit + update, and a
typo in a reference is an ImportError at import time rather than a silent
wrong-URL at runtime.

Conventions:
  * ``*_HOST`` constants are BARE hostnames (no scheme) — use these for
    allowlist / SSRF host checks (``urlsplit(u).hostname == ExternalURL.X_HOST``).
  * Base constants are full ``scheme://host[/path]`` with NO trailing slash —
    callers append ``"/" + path`` (or build an f-string off them).

When you add a third-party integration whose base URL / host appears MORE THAN
ONCE, add a constant here and reference it instead of inlining the literal. See
the "Repeated external URLs / hosts" rule in CLAUDE.md.
"""
from __future__ import annotations


class ExternalURL:
    """Named external URLs + hostnames. Plain ``str`` values — reference as
    ``ExternalURL.GITHUB`` and build paths off them (``f"{ExternalURL.GITHUB}/{owner}/{repo}"``)."""

    # --- GitHub ---------------------------------------------------------
    GITHUB = "https://github.com"
    GITHUB_API = "https://api.github.com"
    GITHUB_HOST = "github.com"

    # --- TMDB / The Movie Database (poster art + metadata) --------------
    # Host for the SPA image-proxy allowlist; base for building poster URLs.
    TMDB_IMAGE_HOST = "image.tmdb.org"
    TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"
    THEMOVIEDB_HOST = "themoviedb.org"

    # --- Plex ----------------------------------------------------------
    PLEX_TV = "https://plex.tv"
    PLEX_TV_HOST = "plex.tv"
    PLEX_DIRECT_HOST = "plex.direct"
