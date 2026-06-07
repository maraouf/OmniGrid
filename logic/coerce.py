"""Shared numeric-coercion helpers for INSERT-row builders.

Dependency-free leaf module (imports only ``typing``) so every sampler /
parser can import it without risking an import cycle. This is the
consolidation of the previously per-module ``_safe_int`` / ``_safe_float``
/ ``_int_or_none`` / ``_float_or_none`` helpers that were duplicated
byte-for-byte across the sampler modules.

Why these exist: Pyright doesn't narrow ``stats.get('x') or 0`` to a
concrete ``int`` when the dict's value type is ``Any`` — the ``or``
expression's static type stays ``Any | int`` and the ``int(...)`` call
complains about ``Any | None``. These helpers centralise the coercion
with explicit narrowing so call sites stay one-liners + lint-clean.

Two flavours per numeric type:
  * ``safe_int`` / ``safe_float`` return a caller-supplied ``default``
    (0 / 0.0) on None / unparseable input — use when the column is NOT
    NULL and a sentinel zero is the right "absent" value.
  * ``int_or_none`` / ``float_or_none`` return ``None`` instead — use
    when NULL carries the semantic "field genuinely absent" (vs an
    explicit zero).

NOT the same as ``logic.service_catalog._coerce_int``, which additionally
rejects ``bool`` and empty-string inputs for its JSON-payload path — that
one keeps its own implementation. Consumers alias these to the legacy
underscore names at import (``from logic.coerce import safe_int as
_safe_int``) so existing call sites don't change.
"""
from __future__ import annotations

from typing import Any, Optional


def safe_int(v: Any, default: int = 0) -> int:
    """Coerce ``v`` to ``int`` or return ``default`` on None / parse failure."""
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def safe_float(v: Any, default: float = 0.0) -> float:
    """Coerce ``v`` to ``float`` or return ``default`` on None / parse failure."""
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def int_or_none(v: Any) -> Optional[int]:
    """Like :func:`safe_int` but returns ``None`` for missing / unparseable
    input — for INSERT columns where NULL means "field genuinely absent"."""
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def float_or_none(v: Any) -> Optional[float]:
    """Companion to :func:`int_or_none` for float fields."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def as_list(v: Any) -> list:
    """``v`` if it's a ``list``, else a new empty ``list``. Single-eval narrowing
    helper that replaces the ``x.get(k) if isinstance(x.get(k), list) else []``
    idiom — that idiom RE-evaluates the call, so the type checker can't narrow it
    and the result keeps the widened ``list | None | Any`` type (a downstream
    ``set()`` / ``for`` / ``|`` then warns 'Expected Iterable, got list | None |
    Any'). The explicit ``-> list`` return type gives consumers a real ``list``,
    and the single evaluation is the actual fix. Use inline anywhere — dict
    literals, for-loops, assignments."""
    return v if isinstance(v, list) else []


def as_dict(v: Any) -> dict:
    """``v`` if it's a ``dict``, else a new empty ``dict`` — the dict companion to
    :func:`as_list` (same single-eval narrowing rationale)."""
    return v if isinstance(v, dict) else {}
