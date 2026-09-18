"""A multi-arch tag's index digest moves when ANY architecture in it moves.

`caddy:2-alpine` was republished with new arm/v6, arm/v7 and arm64/v8 images
and an UNCHANGED amd64 image. The tag's index digest therefore changed, and an
amd64-only Swarm running the previous index was told an update was available
for bytes it was already running — same sub-manifest, same config, same
`created`, same Caddy version.

`same_image_for_platforms` answers the narrower question the operator actually
has: not "did the tag move?" but "did the image *I run* move?". It is asked
only for rows that would otherwise report an update, so the common path pays
nothing.

The fixtures below are the real shapes from Docker Hub — the digests are the
ones from that incident, including the `os: unknown` attestation entries that
ride along with a rebuild and are exactly what must NOT be compared.
"""
from __future__ import annotations

import asyncio

import pytest

from logic import registry

_AMD64 = "sha256:98eb57d882ccd5213d1688764db10c1ca2c58a1ca3a6717a3411ad798f7a423a"
_ARM64_OLD = "sha256:1172d4213087d3fc30bafc7ff2c2896180eb0c41ff7f75f315568fb36cabdcba"
_ARM64_NEW = "sha256:c802bf2721a427e961b9fd6a194c3888a180de9990b43f8530a02bd6be017e17"
_OLD_INDEX = "sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648"
_NEW_INDEX = "sha256:ad27e531c8b286ff153c0e6e16587a1583e4111bb58c4d83bd73d6d3ef0a0ce1"


def _index(amd64: str, arm64: str) -> dict:
    """An OCI index the shape Docker Hub actually serves for caddy:2-alpine —
    real platforms plus the attestation entries that carry os/arch unknown."""
    return {
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            {"digest": amd64, "platform": {"os": "linux", "architecture": "amd64"}},
            {"digest": arm64,
             "platform": {"os": "linux", "architecture": "arm64", "variant": "v8"}},
            {"digest": "sha256:" + "a" * 64,
             "platform": {"os": "unknown", "architecture": "unknown"}},
            {"digest": "sha256:" + "b" * 64,
             "platform": {"os": "unknown", "architecture": "unknown"}},
        ],
    }


class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload
        self.headers: dict[str, str] = {}

    def json(self):
        return self._payload


class _FakeClient:
    """Serves a canned body per manifest digest and counts the fetches, so a
    test can assert the cheap path made no request at all."""

    def __init__(self, by_digest: dict[str, object]):
        self._by_digest = by_digest
        self.calls: list[str] = []

    async def get(self, url, headers=None, follow_redirects=False):
        digest = url.rsplit("/", 1)[-1]
        self.calls.append(digest)
        body = self._by_digest.get(digest)
        if body is None:
            return _Resp({}, status=404)
        return _Resp(body)


def _ask(client, platforms, local=_OLD_INDEX, remote=_NEW_INDEX):
    return asyncio.run(registry.same_image_for_platforms(
        client, "caddy:2-alpine", local, remote, platforms))


@pytest.fixture(autouse=True)
def _clear_cache():
    """The platform map is cached by index digest; without clearing, one test's
    fixture answers the next test's lookup."""
    registry._platform_cache.clear()
    yield
    registry._platform_cache.clear()


def _both_indexes() -> _FakeClient:
    return _FakeClient({
        _OLD_INDEX: _index(_AMD64, _ARM64_OLD),
        _NEW_INDEX: _index(_AMD64, _ARM64_NEW),
    })


# --- the incident ------------------------------------------------------


def test_the_architecture_that_did_not_move_reads_up_to_date():
    """The whole point: amd64 is byte-identical across the two indexes."""
    assert _ask(_both_indexes(), ["linux/amd64"]) is True


def test_the_architecture_that_moved_still_reports_an_update():
    """The guard against over-correcting into a false 'up to date' — arm64
    genuinely changed and must still be reported."""
    assert _ask(_both_indexes(), ["linux/arm64"]) is False


def test_a_service_spanning_both_reports_an_update():
    """A mixed-arch service is only current when EVERY architecture it runs on
    is current. One stale placement makes the service stale."""
    assert _ask(_both_indexes(), ["linux/amd64", "linux/arm64"]) is False


# --- the uname / OCI spelling gap --------------------------------------


@pytest.mark.parametrize("uname,expected", [
    ("x86_64", "linux/amd64"),
    ("aarch64", "linux/arm64"),
    ("armv7l", "linux/arm/v7"),
    ("armv6l", "linux/arm/v6"),
    ("i686", "linux/386"),
    ("amd64", "linux/amd64"),
])
def test_docker_spellings_normalise_to_the_oci_spelling(uname, expected):
    """Docker's node description and an OCI index disagree about how to write
    an architecture. Comparing them raw means the lookup never matches and the
    check degrades to a silent no-op that still LOOKS wired."""
    assert registry.normalize_platform("linux", uname) == expected


@pytest.mark.parametrize("uname", ["aarch64", "arm64", "armv8l"])
def test_every_arm64_spelling_lands_on_the_key_the_index_uses(uname):
    """A Pi reports `aarch64` (no variant), another node reports `armv8l`
    (variant v8), and the index writes arm64 + v8. All three have to collapse
    to ONE string or the lookup misses — and a miss is indistinguishable from
    'cannot prove equal', so the fix would do nothing on arm64 while still
    looking wired."""
    node_key = registry.normalize_platform("linux", uname)
    index_key = registry.normalize_platform("linux", "arm64", "v8")
    assert node_key == index_key, f"{uname!r} -> {node_key!r} != index {index_key!r}"


def test_an_arm64_node_resolves_an_index_that_writes_the_v8_variant():
    """End-to-end for the spelling gap: the fixture index writes arm64 + v8,
    the node asks with the Pi's `aarch64` spelling, and arm64 is unchanged
    between the two indexes — so this must come back up-to-date."""
    plat = registry.normalize_platform("linux", "aarch64")
    same_arm = _FakeClient({
        _OLD_INDEX: _index(_AMD64, _ARM64_NEW),
        _NEW_INDEX: _index("sha256:" + "c" * 64, _ARM64_NEW),
    })
    assert _ask(same_arm, [plat]) is True, "the arm64 spelling did not resolve"


# --- everything unproven stays an update -------------------------------


def test_attestation_entries_are_never_compared():
    """os=unknown sub-manifests are rebuilt whenever anything is, so comparing
    them reintroduces the false positive one level down. They must not appear
    in the platform map at all."""
    client = _both_indexes()
    m = asyncio.run(registry._index_platform_map(
        client, "registry-1.docker.io", "library/caddy", _NEW_INDEX))
    assert m is not None
    assert all(not k.startswith("unknown") for k in m), m
    assert "linux/amd64" in m


def test_a_single_arch_manifest_proves_nothing():
    """A plain manifest has no per-platform chain. Returning True here would
    call every single-arch image up to date regardless of its digest."""
    flat = _FakeClient({
        _OLD_INDEX: {"mediaType": "application/vnd.oci.image.manifest.v1+json",
                     "config": {}, "layers": []},
        _NEW_INDEX: _index(_AMD64, _ARM64_NEW),
    })
    assert _ask(flat, ["linux/amd64"]) is False


def test_a_platform_absent_from_the_index_proves_nothing():
    assert _ask(_both_indexes(), ["linux/s390x"]) is False


def test_no_known_platforms_proves_nothing():
    """An item whose node architecture could not be resolved must fall back to
    the plain digest comparison rather than be assumed current."""
    assert _ask(_both_indexes(), []) is False
    assert _ask(_both_indexes(), [""]) is False


def test_a_missing_digest_proves_nothing():
    assert _ask(_both_indexes(), ["linux/amd64"], local="") is False
    assert _ask(_both_indexes(), ["linux/amd64"], remote="") is False


def test_identical_digests_short_circuit_without_touching_the_registry():
    """The cheap path. Equal digests are already up to date, and the caller
    only asks when they differ — but the guard keeps a future caller from
    paying two GETs to be told what it already knew."""
    client = _both_indexes()
    assert _ask(client, ["linux/amd64"], local=_NEW_INDEX, remote=_NEW_INDEX) is True
    assert client.calls == [], f"made registry calls it did not need: {client.calls}"


def test_an_unreachable_index_proves_nothing():
    """A 404 / transient failure must not be read as agreement."""
    missing = _FakeClient({_NEW_INDEX: _index(_AMD64, _ARM64_NEW)})
    assert _ask(missing, ["linux/amd64"]) is False
