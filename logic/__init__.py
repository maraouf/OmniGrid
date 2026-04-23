"""Modular business logic for OmniGrid.

main.py stays the thin shell that owns the FastAPI app, routes, and
lifespan. Everything else lands here in focused modules:

  auth.py     — identity, sessions, API tokens, middleware, role deps
  metrics.py  — Prometheus registry, metric definitions, cache-age collector

Future candidates (not yet extracted): gather, stats, ops, db, portainer,
registry. Extract when the module has a clean boundary and shared state
can be expressed as a dependency rather than an import cycle.
"""
