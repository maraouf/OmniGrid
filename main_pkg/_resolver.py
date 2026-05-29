"""Cross-module dynamic name resolver for ATTRIBUTE-ACCESS lookups only.

Scope (narrow): handles failed ``module.X`` attribute lookups +
``from module import X`` semantics across the ``main.py`` →
``main_pkg/*`` split, per PEP 562's module-level ``__getattr__`` hook.
When a caller does ``getattr(main_pkg.hosts_routes, "_some_helper")``
and the name isn't in that module's ``__dict__``, the module's
``__getattr__`` fires + delegates here; we walk ``main`` + every loaded
``main_pkg.*`` sibling's ``__dict__`` looking for the name.

**Out of scope:** bare ``LOAD_GLOBAL`` references inside function
bodies — Python's bytecode interpreter does NOT route LOAD_GLOBAL
through the module's ``__getattr__``. A function in module B that
references ``_helper`` (defined in module A) raises ``NameError`` at
call time even when B has a ``__getattr__`` that would resolve it.
That gap is what the centralized ``_wire_cross_module_underscore_globals()``
block at ``main.py``'s tail covers: it eagerly copies every
cross-module underscore-prefixed symbol into the consumer's
``__dict__`` so bare LOAD_GLOBAL resolves locally without bouncing
through any dynamic-resolution layer. See the project conventions "Cross-module
underscore-name LOAD_GLOBAL leaks" for the full rule.

This resolver remains load-bearing for the ATTRIBUTE-ACCESS path —
e.g. the ``main_pkg/apps_routes.py:_load_hosts_config`` lazy
delegate uses ``getattr(main, "_load_hosts_config")`` for runtime
resolution; that PATH still benefits from the resolver. It is NOT
the safety net for function-body LOAD_GLOBAL references; the
wire-fixer at ``main.py``'s tail is.

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
