from logic import oidc_providers as P


def test_blank_keeps_the_legacy_redirect_host_match():
    assert P.host_allowed("", "https://a.example/api/oidc/cb", "a.example")
    assert not P.host_allowed("", "https://a.example/api/oidc/cb", "b.example")
    assert P.host_allowed("", "", "anything")


def test_listed_hosts_show_on_each_domain_and_nowhere_else():
    hosts = "omnigrid.home.lan, https://omnigrid.example.com:8443/x"
    assert P.host_allowed(hosts, "https://omnigrid.home.lan/cb", "omnigrid.example.com")
    assert P.host_allowed(hosts, "https://omnigrid.home.lan/cb", "OMNIGRID.HOME.LAN")
    assert not P.host_allowed(hosts, "https://omnigrid.home.lan/cb", "other.example")


def test_star_shows_everywhere():
    assert P.host_allowed("*", "https://a.example/cb", "b.example")
    assert P.parse_login_hosts("a.example a.example,B.example") == ["a.example", "b.example"]


# ---- profile picture from the provider ------------------------------------
import asyncio
import contextlib

from logic import oidc


class _Resp:
    def __init__(self, status=200, body=b"", ctype="image/png", js=None):
        self.status_code, self.content, self._js = status, body, js
        self.headers = {"content-type": ctype}

    def json(self):
        return self._js


def _run_sync(monkeypatch, tmp_path, responses, claims, existing_avatar=None):
    from logic import auth, backups
    calls, saved = [], {}

    class _Client:
        def __init__(self, **kw):
            assert kw.get("follow_redirects") is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            calls.append(url)
            return responses[url]

    monkeypatch.setattr(oidc.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(backups, "AVATAR_DIR", str(tmp_path))
    monkeypatch.setattr(oidc, "db_conn", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(auth, "get_user_profile", lambda c, i: {"avatar_path": existing_avatar})
    monkeypatch.setattr(auth, "set_user_avatar_path", lambda c, i, p: saved.update(path=p))
    prov = oidc._providers.require("authentik")
    asyncio.run(oidc._sync_avatar(prov, 7, claims, "tok",
                                  {"userinfo_endpoint": "https://idp/userinfo"}, True))
    return calls, saved


def test_picture_in_id_token_is_stored(monkeypatch, tmp_path):
    calls, saved = _run_sync(monkeypatch, tmp_path,
                             {"https://idp/p.png": _Resp(body=b"PNG")},
                             {"picture": "https://idp/p.png"})
    assert saved["path"] == "u7.png" and (tmp_path / "u7.png").read_bytes() == b"PNG"
    assert calls == ["https://idp/p.png"]


def test_picture_falls_back_to_userinfo(monkeypatch, tmp_path):
    _, saved = _run_sync(monkeypatch, tmp_path, {
        "https://idp/userinfo": _Resp(js={"picture": "https://idp/a.jpg"}),
        "https://idp/a.jpg": _Resp(body=b"J", ctype="image/jpeg")}, {})
    assert saved["path"] == "u7.jpg"


def test_uploaded_avatar_is_never_overwritten_and_bad_pictures_are_skipped(monkeypatch, tmp_path):
    calls, saved = _run_sync(monkeypatch, tmp_path, {}, {"picture": "https://idp/p.png"},
                             existing_avatar="u7.png")
    assert calls == [] and not saved
    _, saved = _run_sync(monkeypatch, tmp_path,
                         {"https://idp/p": _Resp(body=b"<html>", ctype="text/html")},
                         {"picture": "https://idp/p"})
    assert not saved


def test_session_is_minted_in_the_sign_in_path_not_a_swallowed_handler():
    """The avatar step once landed in the middle of the sign-in `with` block,
    which pushed the session minting into its `except` — every SSO login then
    died with UnboundLocalError on `cookie_value`. Those calls must sit under
    the `with`, never under an exception handler."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(oidc))
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    wanted = {"touch_last_login", "create_session"}
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in wanted and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "auth":
            chain, cur = [], node
            while cur in parents:
                cur = parents[cur]
                chain.append(type(cur).__name__)
            if "AsyncFunctionDef" in chain and "With" in chain:
                assert "ExceptHandler" not in chain, node.func.attr
                found.add(node.func.attr)
    assert found == wanted
