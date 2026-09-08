"""The SPA shell is reachable with a valid bearer token, not only a cookie.

`_shell_or_login` exists to stop an unauthenticated BROWSER seeing the
dashboard paint before it bounces to /login. It only ever inspected the session
cookie, so a machine client presenting a valid API token was redirected to a
login form it can never complete — the assembled page was unreachable to every
non-cookie caller.

That is not merely inconvenient. The include expander's failure mode is to
STRIP markers it cannot resolve: markup disappears from the page and the only
outward sign is a single log line. Fetching what the server actually assembled
is the one way to see that, and it was closed off.

This is not a widening of access. A token holder is already authenticated and
can read the data behind /api/*; the shell is markup and carries none — the
same page a read-only cookie session is served today. The tests below pin both
directions: a good token gets in, and everything else is still turned away.
"""
from __future__ import annotations

import inspect
import pathlib
import re

from main_pkg import users_routes

SRC = inspect.getsource(users_routes._shell_or_login)
ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_the_gate_consults_the_bearer_token():
    assert "verify_api_token" in SRC, (
        "_shell_or_login no longer checks the bearer token — every machine "
        "client is bounced to a login form it cannot complete")


def test_the_bearer_check_runs_before_the_cookie_check():
    """A token-only caller has no cookie; if the cookie branch ran first it
    would fall straight through to the redirect."""
    assert SRC.index("verify_api_token") < SRC.index("parse_session_cookie"), (
        "the cookie branch precedes the bearer branch")


def test_only_a_VALID_token_is_accepted():
    """The gate must turn on the verifier's result, never on the mere presence
    of an Authorization header — otherwise `Bearer nonsense` opens the shell."""
    assert re.search(r"verify_api_token\(.*\)\s+is not None", SRC), (
        "the bearer branch does not test the verifier's return value")


def test_a_failed_token_lookup_falls_through_rather_than_serving():
    """A DB error resolving the token must NOT serve the shell on its own — it
    falls through to the cookie check, which owns the permissive behaviour.
    The two failure policies are deliberately different: an unresolvable
    COOKIE serves the shell (locking people out of their own dashboard over a
    transient SQLite blip is worse than a flash), while an unresolvable TOKEN
    simply stops being evidence of anything."""
    bearer = SRC[SRC.index("Bearer "):SRC.index("parse_session_cookie")]
    assert "return None" not in bearer.split("except")[-1], (
        "the bearer except-branch serves the shell; it must fall through")


def test_the_redirect_is_still_there_for_an_anonymous_browser():
    assert "RedirectResponse" in SRC and "/login?next=" in SRC, (
        "the anonymous-browser redirect is gone — the dashboard would flash "
        "before bouncing, which is the whole reason this function exists")


def test_the_shell_is_a_static_template_with_only_the_version_filled_in():
    """The argument for serving this to a token holder is that it is markup:
    the renderer substitutes exactly one thing, the build version, and never
    interpolates per-caller or per-fleet data.

    An earlier version of this test grepped the HTML for words like "password"
    and failed on four PROSE hits — a code comment and an error string. Words
    are not data; what matters is whether the SERVER puts anything caller-
    specific into the response, so this asserts on the renderer instead.
    """
    src = inspect.getsource(users_routes._render_shell)
    subs = re.findall(r'\.replace\(\s*"(?P<marker>[^"]+)"', src)
    assert subs == ["__APP_VERSION__"], (
        f"_render_shell now substitutes {subs} — anything beyond the version "
        f"means the shell is no longer caller-independent, and serving it to a "
        f"token holder needs a fresh look")
