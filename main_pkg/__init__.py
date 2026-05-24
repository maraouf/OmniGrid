"""`main_pkg` — sibling package to `main.py` for splitting the
monolithic main module. Each sub-module re-exports its symbols
via star-import so the FastAPI route decorators register against
the single `app` instance defined in `main.py`. See each
sub-module's docstring for the loading-order contract.
"""
