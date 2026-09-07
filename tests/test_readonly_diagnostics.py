"""Pin which diagnostic endpoints a readonly caller may read.

A readonly API token is the right shape for an automated or assistive client:
it can see everything and change nothing. But the diagnostics were admin-gated,
so such a token could read every host's telemetry and NOT the logs explaining
why a host was failing, nor the record of what the AI had done. The read-only
client was locked out of precisely the reads it exists to perform.

These four moved to "any authenticated caller". The test that matters is the
other half: the writes next to them must NOT have moved. A future edit that
copies the wrong dependency onto `api_logs_clear` would let a readonly token
erase the log buffer, and nothing else in the suite would notice.
"""
from __future__ import annotations

import inspect
import re

# `main` must be imported FIRST: the main_pkg modules resolve through its
# star-import chain, so importing one directly is a circular import.
import main  # noqa: F401  (import for side effect: builds the facade)
from main_pkg import admin_ai_routes as ai_routes
from main_pkg import scan_routes

# (module, function name) -> the dependency its signature must carry.
READ_OPEN = [
    (ai_routes, "api_admin_ai_jobs"),
    (scan_routes, "api_admin_logs_files"),
    (scan_routes, "api_admin_logs_file_view"),
    (scan_routes, "api_admin_logs_file_download"),
]

STILL_ADMIN = [
    (scan_routes, "api_logs_clear"),
]


def _signature_of(mod, name: str) -> str:
    fn = getattr(mod, name, None)
    assert fn is not None, f"{mod.__name__}.{name} no longer exists"
    src = inspect.getsource(fn)
    # Everything up to the first `):` that closes the parameter list.
    return src[: src.index("):")]


def test_the_read_only_diagnostics_accept_any_authenticated_caller():
    for mod, name in READ_OPEN:
        sig = _signature_of(mod, name)
        assert "AuthedUser" in sig, (
            f"{name} no longer accepts a readonly caller — a readonly token is "
            f"locked out of the diagnostics it exists to read")
        assert "AdminUser" not in sig, f"{name} still carries the admin guard"


def test_the_writes_beside_them_are_still_admin_only():
    """The load-bearing half.

    Opening a read is safe; opening the write next to it is not. Clearing the
    log buffer destroys the very evidence the read endpoints serve.
    """
    for mod, name in STILL_ADMIN:
        sig = _signature_of(mod, name)
        assert "AdminUser" in sig, (
            f"{name} is a WRITE and must stay admin-only")
        assert "AuthedUser" not in sig, (
            f"{name} was opened to readonly — a readonly token could now "
            f"destroy log evidence")


def test_the_authed_alias_does_not_check_a_role():
    """AuthedUser must mean "signed in", not "signed in as anything special" —
    otherwise the change above silently does nothing."""
    alias_src = inspect.getsource(main)
    m = re.search(r"^AuthedUser\s*=\s*Annotated\[[^\]]+\]", alias_src, re.M)
    assert m, "AuthedUser alias is gone"
    assert "current_user" in m.group(0), (
        "AuthedUser must depend on current_user, which authenticates without "
        "requiring a role")
