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
