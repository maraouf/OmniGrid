"""Tests for OmniGrid not reporting an update to the build it just deployed.

The report: right after a deploy, OmniGrid's own row showed UPDATE. Measured
against the live instance, the running service carried the correct digest for
the new build and the registry agreed with it — the digest OmniGrid called
"remote" belonged to the PREVIOUS build. A forced refresh corrected the row to
up-to-date, so nothing was mispinned; the verdict was simply old.

Two things kept it old, and either alone is enough to reproduce it:

1. Seeding `_cache` from `items_snapshot` at boot carried the snapshot's own
   timestamp over. `/api/items` refreshes on `(now - _cache["ts"]) > TTL`, so
   a container that had just replaced its predecessor counted that
   predecessor's gather as its own and skipped the refresh for the rest of a
   900s window. The seed exists to be replaced promptly; the inherited
   timestamp meant nothing replaced it.

2. The registry digest cache is persisted and warmed at boot. `:latest` is
   precisely the tag that moves while the process is down — and here the
   restart was CAUSED by it moving — so warming that entry re-asserted the
   pre-deploy digest even once a gather did run.
"""
from __future__ import annotations

import inspect

from logic import gather, registry


def test_a_seeded_snapshot_does_not_present_as_a_fresh_gather():
    """The staleness check must see the seed as stale, or no refresh runs."""
    src = inspect.getsource(gather.seed_items_cache_from_snapshot)
    assert '_cache["ts"] = 0.0' in src, (
        "the boot seed sets a real cache timestamp again — a container that "
        "just deployed itself will skip its first refresh and keep serving "
        "the previous container's verdict")
    assert '_cache["_snapshot_ts"]' in src, (
        "the snapshot's own timestamp is no longer preserved anywhere")


def test_the_seed_still_paints_instantly():
    """Marking it stale must not turn the cold path into a blocking gather —
    `/api/items` only blocks when there is nothing cached to serve."""
    src = inspect.getsource(gather.seed_items_cache_from_snapshot)
    assert '_cache["items"] = items' in src, (
        "the seed no longer populates items, so `has_cached_data` is false "
        "and the first request blocks instead of painting")


def test_moving_tags_are_not_warmed_across_a_restart():
    src = inspect.getsource(registry.seed_digest_cache_from_db)
    assert "_tag_is_moving(key)" in src, (
        "the boot warm no longer skips moving tags — a redeployed :latest "
        "will be compared against its pre-restart digest")


def test_which_tags_count_as_moving():
    moving = ["reg|ns/img|latest", "reg|ns/img|main", "reg|ns/img|edge",
              "reg|ns/img|nightly", "reg|ns/img|stable", "reg|ns/img|LATEST"]
    for key in moving:
        assert registry._tag_is_moving(key), f"{key!r} should be treated as moving"


def test_a_pinned_release_still_gets_the_cache():
    """The whole value of the persisted cache is not re-HEADing every image
    at boot. An immutable tag cannot have moved, so it keeps that."""
    for key in ("reg|ns/img|1.6.16", "reg|ns/omnigrid|2.0.25", "reg|ns/img|v1.2.3"):
        assert not registry._tag_is_moving(key), f"{key!r} should stay cached"


def test_a_version_line_counts_as_moving():
    """`3` and `3.11` follow their newest patch, so they move like `latest`."""
    for key in ("reg|ns/img|3", "reg|ns/img|3.11", "reg|ns/img|v2"):
        assert registry._tag_is_moving(key), f"{key!r} follows a line and moves"


def test_an_empty_tag_is_treated_as_moving():
    """No tag means implicit `latest`; guessing immutable would be the unsafe
    direction, since the cost of being wrong is a false update verdict."""
    assert registry._tag_is_moving("reg|ns/img|")
    assert registry._tag_is_moving("")
