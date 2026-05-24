"""Cross-module dynamic name resolver for underscore-prefixed leaks.

After the ``main.py`` → ``main_pkg/*`` split, ``from main import *`` at
the top of every child module silently DROPS underscore-prefixed names
per Python's star-import spec. The pre-split codebase referenced ~50
such helpers freely across the chain (``_node_attr``,
``_load_hosts_config``, ``_kick_background_gather``, etc.); post-split
every consumer raised ``NameError`` at call time.

This module exposes ``resolve(consumer_module_name, name)`` — the
implementation behind every module-level ``__getattr__`` injected at
the tail of ``main.py`` + each ``main_pkg/*.py``. PEP 562 (Python
3.7+) routes failed module-attribute lookups through the module's
``__getattr__``; we delegate here so the resolver lives in ONE place
instead of being copy-pasted 12 times.

Resolution order: ``main`` first (most underscore helpers live
there), then every loaded ``main_pkg.*`` sibling. Reads via the
target module's ``__dict__`` directly so we don't bounce through the
target's own ``__getattr__`` (would recurse on a true miss). Per-
``(consumer, name)`` recursion guard catches A→B→A cycles when
neither side defines the name.
"""
from __future__ import annotations

import sys

# Per-(consumer, name) in-flight set. Threading-safe enough for our
# single-replica deployment (CPython set ops are atomic under the GIL).
_LOOKUP_GUARD: set[tuple[str, str]] = set()


def resolve(consumer_module: str, name: str):
    """Look up ``name`` in ``main`` + every loaded sibling ``main_pkg.*``.

    Raises ``AttributeError`` on a true miss so the consumer module's
    ``__getattr__`` returns cleanly (and Python then raises the
    original ``NameError`` from the caller's frame).
    """
    if not (name.startswith("_") and not name.startswith("__")):
        raise AttributeError(name)
    key = (consumer_module, name)
    if key in _LOOKUP_GUARD:
        raise AttributeError(name)
    _LOOKUP_GUARD.add(key)
    try:
        # `main` first: most underscore helpers live there.
        candidates = ("main",) + tuple(
            n for n in tuple(sys.modules)
            if n.startswith("main_pkg.") and n != consumer_module
        )
        for mod_name in candidates:
            mod = sys.modules.get(mod_name)
            if mod is None:
                continue
            val = mod.__dict__.get(name)
            if val is not None:
                return val
        raise AttributeError(name)
    finally:
        _LOOKUP_GUARD.discard(key)
