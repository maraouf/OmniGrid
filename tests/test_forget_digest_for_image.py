"""An update about to pull an image makes the next check re-read the registry."""
import time

from logic import registry


def test_forget_drops_only_that_images_cached_digest():
    reg, repo, tag = registry.parse_image_ref("git.example.lan/owner/app:latest")
    other = f"{reg}|{repo}|1.0"
    mine = f"{reg}|{repo}|{tag}"
    registry._digest_cache[mine] = ("sha256:old", time.time())
    registry._digest_cache[other] = ("sha256:keep", time.time())
    try:
        registry.forget_digest_for_image("git.example.lan/owner/app:latest")
        assert mine not in registry._digest_cache
        assert other in registry._digest_cache
    finally:
        registry._digest_cache.pop(mine, None)
        registry._digest_cache.pop(other, None)


def test_unparseable_ref_is_ignored():
    registry.forget_digest_for_image("")
