"""The GitHub same-path guess is for Docker Hub images only."""
import asyncio

from logic import registry


def _notes(monkeypatch, image):
    async def no_labels(client, ref):
        return {}
    monkeypatch.setattr(registry, "_fetch_image_config_labels", no_labels)
    registry._release_notes_cache.clear()
    return asyncio.run(registry._get_release_notes_impl(image))


def test_self_hosted_image_is_not_pointed_at_github(monkeypatch):
    out = _notes(monkeypatch, "git.example.lan/someone/app:1.0")
    assert out["ok"] is False
    assert out["source_url"] == ""


def test_hub_image_still_guesses_github(monkeypatch):
    async def no_github(*a, **k):
        return None
    monkeypatch.setattr(registry, "_fetch_github_release_notes", no_github)
    monkeypatch.setattr(registry, "_fetch_github_latest_release", no_github)
    out = _notes(monkeypatch, "someone/app:1.0")
    assert "github.com/someone/app" in (out.get("source_url") or "")
