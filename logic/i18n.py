"""Server-side i18n loader — backend translator for notifications,
emails, and any other surface that fires text TO operators (or via
their authenticated channel) where the SPA's `t()` helper isn't in
play.

Loads the same `static/i18n/<locale>.json` bundles the SPA uses, so
the en.json file stays the single source of truth across both
sides. Lazy-loaded on first access; subsequent calls hit the cache.

Public surface:

* :func:`tr(key, locale="en", **placeholders) -> str` — resolve a
  dot-path key against the bundle (e.g. `"notifications.events.
  port_scan_new_port.title"`). Falls back to ``en`` when the locale
  doesn't have the key, then to the literal key string if even
  English is missing (visible "missing-translation" indicator).
  ``{placeholder}`` substitution is performed via ``str.format_map``
  with a forgiving missing-key handler so a typo'd placeholder
  renders as ``{key}`` verbatim instead of raising ``KeyError``
  mid-notification.

* :func:`available_locales() -> list[str]` — list of locale codes
  the bundle dir exposes. Reads `static/i18n/index.json` for the
  canonical "supported locales" list rather than scanning the dir
  (matches the SPA's discovery path).

* :func:`pick_locale(*candidates) -> str` — first candidate that
  has a loaded bundle, else "en". Use this when the operator's
  locale could come from multiple sources (ui_prefs / Accept-
  Language header / explicit override).

Cache invalidation: bundles are loaded once at module import time
and stay resident — file mtime changes are NOT auto-detected. A
container restart picks up updates. This matches the SPA's behaviour
(bundles fetch once per page load) and keeps the lookup hot-path
allocation-free.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

# Resolve the bundle root relative to the module's location so the
# import doesn't break when CWD changes (uvicorn / pytest / scripts).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_I18N_DIR = _REPO_ROOT / "static" / "i18n"

# In-memory cache. Keyed by locale code; value is the parsed JSON.
_BUNDLES: dict[str, dict] = {}
_LOAD_FAILED: set[str] = set()  # locales we tried + failed to load


def _load_bundle(locale: str) -> Optional[dict]:
    """Lazy-load `static/i18n/<locale>.json`. Returns the parsed dict
    or ``None`` when the file is missing / unparseable. Failures are
    cached in ``_LOAD_FAILED`` so we don't retry every call.
    """
    if locale in _BUNDLES:
        return _BUNDLES[locale]
    if locale in _LOAD_FAILED:
        return None
    if not locale or not re.match(r"^[a-z][a-z0-9_-]*$", locale):
        _LOAD_FAILED.add(locale)
        return None
    path = _I18N_DIR / f"{locale}.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        print(f"[i18n] failed to load locale={locale!r}: {e}")
        _LOAD_FAILED.add(locale)
        return None
    if not isinstance(data, dict):
        _LOAD_FAILED.add(locale)
        return None
    _BUNDLES[locale] = data
    return data


def _resolve_path(bundle: dict, key: str) -> Optional[str]:
    """Walk the dot-path key against the nested bundle. Returns the
    leaf string when found, ``None`` when any segment is missing OR
    when the leaf isn't a string.
    """
    cur: Any = bundle
    for seg in key.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(seg)
        if cur is None:
            return None
    return cur if isinstance(cur, str) else None


class _SafeMissing(dict):
    """`str.format_map` helper that renders missing placeholders as
    ``{key}`` instead of raising ``KeyError``. Same shape as the
    SPA's `_renderTemplate` resilience — operator-set templates
    that reference an unknown placeholder still render the rest of
    the string visibly.
    """

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def tr(key: str, locale: str = "en", **placeholders: Any) -> str:
    """Resolve `key` against `<locale>` bundle (with `en` fallback).

    Returns the rendered string with `{placeholder}` substitutions
    applied. When the key is missing in BOTH the requested locale
    AND `en`, returns the key itself so the missing-translation case
    is visible in production rather than silently rendering an empty
    string.

    Example::

        tr("notifications.events.user_login.title", "en", actor="alice")
        # → "🔓 alice signed in"
    """
    if not key:
        return ""
    # Try the requested locale first, then en.
    for loc in (locale, "en") if locale != "en" else ("en",):
        bundle = _load_bundle(loc)
        if bundle is None:
            continue
        leaf = _resolve_path(bundle, key)
        if leaf is not None:
            try:
                return leaf.format_map(_SafeMissing(placeholders))
            except (ValueError, IndexError):
                # Malformed `{`/`}` in the template (e.g. literal
                # braces from an operator-edited override that
                # leaked into the bundle). Return raw to avoid
                # crashing the notification path.
                return leaf
    return key


def available_locales() -> list[str]:
    """Return the canonical list of supported locales by reading
    `static/i18n/index.json`. Falls back to scanning the bundle
    directory when the index is missing / malformed.
    """
    index_path = _I18N_DIR / "index.json"
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = None
    if isinstance(data, list):
        codes = [
            entry.get("code") for entry in data
            if isinstance(entry, dict) and entry.get("code")
        ]
        if codes:
            return [str(c) for c in codes]
    # Fallback — list `*.json` files in the bundle dir, drop
    # `index.json` itself.
    try:
        files = sorted(
            p.stem for p in _I18N_DIR.iterdir()
            if p.suffix == ".json" and p.stem != "index"
        )
        return files or ["en"]
    except OSError:
        return ["en"]


def pick_locale(*candidates: Optional[str]) -> str:
    """Return the first candidate for which a bundle exists, else
    ``"en"``. Use when the operator's locale could come from
    multiple sources (`ui_prefs.lang` → `Accept-Language` header →
    explicit override → fallback). ``None`` / empty values are
    skipped. Two-letter prefixes (e.g. ``"en-US"`` → ``"en"``) try
    the exact code first, then the prefix.
    """
    for candidate in candidates:
        if not candidate:
            continue
        c = str(candidate).strip().lower()
        if not c:
            continue
        if _load_bundle(c) is not None:
            return c
        # Try the language prefix (e.g. "en-US" → "en")
        if "-" in c:
            prefix = c.split("-", 1)[0]
            if prefix and prefix != c and _load_bundle(prefix) is not None:
                return prefix
    return "en"


def reload_bundles() -> None:
    """Drop the in-memory cache so the next ``tr`` call re-reads
    every bundle from disk. Useful for tests; not normally invoked
    in production (container restart suffices).
    """
    _BUNDLES.clear()
    _LOAD_FAILED.clear()
