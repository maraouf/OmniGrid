"""Which app routes a read-only role may call.

The Apps nav badge counts down apps, and diagnosing it meant asking an admin to
read `/api/apps` because it demanded an admin session — a read-only API token
got a 403 on the one endpoint that names the failing apps. The aggregate carries
status, hosts and URLs, with every secret reduced to a `*_set` flag, so it sits
with the hosts and items reads. The flat instance list feeds the admin editor
and stays admin-only.
"""
from __future__ import annotations

import main
from logic import auth


def _dependency_calls(path: str) -> list:
    for route in main.app.routes:
        if getattr(route, "path", None) == path and "GET" in (getattr(route, "methods", None) or ()):
            return [d.call for d in route.dependant.dependencies]
    raise AssertionError(f"no GET {path} route")


def test_the_apps_aggregate_is_readable_without_an_admin_session():
    assert auth.require_admin not in _dependency_calls("/api/apps")


def test_the_instance_list_behind_the_admin_editor_stays_admin_only():
    assert auth.require_admin in _dependency_calls("/api/apps/instances")
