"""A Beszel Agent chip is recognised so the sampler can trust the hub's
report when the agent runs in WebSocket mode and listens on no port."""
from logic import service_catalog
from logic.service_sampler import resolve_chip_probe_target


def _chip(**kw):
    chip = {
        "name": "Beszel Agent", "icon": "beszel", "url": "http://truenas.lan",
        "probe": {"enabled": True, "ports": [{"port": 45876, "protocol": "tcp"}]},
    }
    chip.update(kw)
    return chip


def _target(chip):
    return resolve_chip_probe_target({"id": "truenas", "beszel_name": "nas"}, 0, chip)


def test_agent_chip_is_flagged_and_carries_hub_name():
    tgt = _target(_chip())
    assert tgt["beszel_agent"] is True
    assert tgt["beszel_name"] == "nas"


def test_hub_chip_is_not_an_agent(monkeypatch):
    monkeypatch.setattr(service_catalog, "get_catalog_by_id",
                        lambda cid: {"slug": "beszel-hub"})
    tgt = _target(_chip(name="Beszel Hub", catalog_id=7))
    assert tgt["beszel_agent"] is False


def test_other_icons_never_flagged():
    assert _target(_chip(icon="plex", name="Plex agent"))["beszel_agent"] is False


def test_beszel_name_falls_back_to_host_id():
    tgt = resolve_chip_probe_target({"id": "truenas"}, 0, _chip())
    assert tgt["beszel_name"] == "truenas"
