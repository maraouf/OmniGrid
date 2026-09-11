"""Release notes are fetched when the update is FOUND, not when it is clicked.

The Update confirm used to open on a "Loading release notes..." spinner and
wait out a cold lookup — registry manifest, config blob, one or two GitHub
calls — for an update the dashboard had known about for hours. The gather
that discovers an update now warms the notes in the background, and the
browser fetches them as soon as the items list shows the update.

Two properties matter beyond "it's faster", and both are about the GitHub
budget (60 unauthenticated calls an hour):
  * each (image, upstream digest) is warmed ONCE, whatever it returned, so a
    failing lookup is not retried every gather;
  * a browser and the background warm asking at the same moment share ONE
    lookup instead of each running the cold chain.
"""
from __future__ import annotations

import asyncio
import pathlib

import pytest

from logic import registry


@pytest.fixture(autouse=True)
def _clean_state():
    registry._release_notes_warmed.clear()
    registry._release_notes_cache.clear()
    registry._release_notes_inflight.clear()
    registry._release_notes_warm_inflight = False
    yield
    registry._release_notes_warmed.clear()
    registry._release_notes_cache.clear()
    registry._release_notes_inflight.clear()
    registry._release_notes_warm_inflight = False


def _item(image, status="update", digest="sha256:new", health="healthy"):
    return {"image": image, "status": status, "remote_digest": digest, "health": health}


def test_only_live_pending_updates_are_selected():
    items = [
        _item("a:latest"),
        _item("b:latest", status="up-to-date"),
        _item("c:latest", health="offline"),
        _item("d:latest", status="error"),
    ]
    assert registry.release_notes_to_warm(items) == [("a:latest", "sha256:new")]


def test_an_image_shared_by_several_services_is_warmed_once():
    items = [_item("a:latest"), _item("a:latest")]
    assert registry.release_notes_to_warm(items) == [("a:latest", "sha256:new")]


def test_the_same_digest_is_not_warmed_twice(monkeypatch):
    calls = []

    async def _impl(image):
        calls.append(image)
        return {"ok": False, "error": "no source label on image"}

    monkeypatch.setattr(registry, "_get_release_notes_impl", _impl)
    targets = registry.release_notes_to_warm([_item("a:latest")])
    asyncio.run(registry.warm_release_notes(targets))
    # A FAILED lookup counts as warmed — the budget-protecting half.
    assert registry.release_notes_to_warm([_item("a:latest")]) == []
    assert calls == ["a:latest"]


def test_a_new_upstream_push_drops_the_old_notes_and_rewarms(monkeypatch):
    calls = []

    async def _impl(image):
        calls.append(image)
        return {"ok": True, "body": "notes"}

    monkeypatch.setattr(registry, "_get_release_notes_impl", _impl)
    asyncio.run(registry.warm_release_notes([("a:latest", "sha256:one")]))
    registry._release_notes_cache["a:latest"] = {"ts": 9e18, "data": {"ok": True, "body": "old"}}
    targets = registry.release_notes_to_warm([_item("a:latest", digest="sha256:two")])
    assert targets == [("a:latest", "sha256:two")]
    asyncio.run(registry.warm_release_notes(targets))
    # The stub doesn't write the cache, so anything left is the stale entry.
    assert "a:latest" not in registry._release_notes_cache, (
        "notes cached for the previous image survived a new push")
    assert calls == ["a:latest", "a:latest"]


def test_nothing_is_selected_while_a_warm_is_running():
    registry._release_notes_warm_inflight = True
    assert registry.release_notes_to_warm([_item("a:latest")]) == []


def test_a_concurrent_request_shares_the_lookup(monkeypatch):
    calls = []

    async def _impl(image):
        calls.append(image)
        await asyncio.sleep(0.02)
        return {"ok": True, "body": "x"}

    a, b = _two_concurrent_requests(monkeypatch, _impl)
    assert calls == ["a:latest"], f"{len(calls)} lookups for one image"
    assert a == b == {"ok": True, "body": "x"}
    assert registry._release_notes_inflight == {}


def _two_concurrent_requests(monkeypatch, impl):
    """Two callers ask for the same image at the same moment."""
    monkeypatch.setattr(registry, "_get_release_notes_impl", impl)

    async def _both():
        return await asyncio.gather(
            registry.get_release_notes("a:latest"),
            registry.get_release_notes("a:latest"),
            return_exceptions=True,
        )

    return asyncio.run(_both())


def test_a_failing_leader_does_not_strand_its_waiter(monkeypatch):
    async def _impl(_image):
        await asyncio.sleep(0.02)
        raise RuntimeError("boom")

    lead, waiter = _two_concurrent_requests(monkeypatch, _impl)
    assert isinstance(lead, RuntimeError)
    assert waiter == {"ok": False, "error": "release-notes lookup failed"}


# --- The SPA half -----------------------------------------------------------

_SPA = (pathlib.Path(__file__).resolve().parents[1]
        / "static" / "js" / "app-drawer-charts.js").read_text(encoding="utf-8")


def _fn(name: str, next_name: str) -> str:
    start = _SPA.index(f"{name}(")
    return _SPA[start:_SPA.index(f"{next_name}(", start + 1)]


def test_the_filler_waits_for_the_popup_before_looking_for_it():
    """With the payload cached, the fetch promise resolves in a microtask —
    BEFORE SweetAlert has inserted the popup. Looking up the placeholder
    then finds nothing and the notes are silently never shown. The frame
    wait has to sit between the fetch and the DOM lookup."""
    body = _fn("async _replaceReleaseNotesAsync", "async updateStack")
    fetched = body.index("await this._fetchReleaseNotes(")
    frame = body.index("requestAnimationFrame")
    lookup = body.index("document.getElementById(")
    assert fetched < frame < lookup, "the DOM is read before the popup can exist"


def test_both_confirm_paths_open_with_the_cached_block():
    ops = (pathlib.Path(__file__).resolve().parents[1]
           / "static" / "js" / "app-ops.js").read_text(encoding="utf-8")
    assert "_releaseNotesBlockHtml(item.image)" in ops
    assert "_releaseNotesBlockHtml(stackImage)" in _SPA


def test_prefetch_is_wired_to_the_items_refresh():
    admin = (pathlib.Path(__file__).resolve().parents[1]
             / "static" / "js" / "app-admin.js").read_text(encoding="utf-8")
    assert "this._prefetchReleaseNotes(incomingItems)" in admin
