"""An icon file on disk is not a rendered icon.

`iconUrlFor` gates its final fallback on `KNOWN_ICONS.has(natural)` — on
purpose, so an unmatched stack name does not fire a 404 for every row.
The cost is that artwork with no entry resolves to `''` and draws a
blank slot with NO error anywhere: no console warning, no 404, no lint
finding. UnifiedSSO shipped that way and was indistinguishable from a
brand OmniGrid simply did not have.

These pin the wiring, not the artwork. The resolver is JS and cannot be
executed from pytest, so — per the project's rule that a test which
MIRRORS a condition cannot detect the divergence it exists to catch —
each assertion here is on the STRUCTURE the wiring depends on (an entry
exists in this table, a file exists at that path) rather than on a
re-implementation of the resolution order.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_ICON_DIR = _ROOT / "static" / "img" / "icons"


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def _set_literal(src: str, name: str) -> set[str]:
    """The slugs inside `export const <name> = new Set([...])`."""
    body = src.split(f"{name} = new Set([", 1)[1].split("])", 1)[0]
    return {m.group("slug") for m in re.finditer(r"'(?P<slug>[a-z0-9-]+)'", body)}


def _icon_files() -> set[str]:
    return {p.stem for p in _ICON_DIR.glob("*.svg")}


def test_every_known_icon_has_a_file_on_disk():
    """The clean direction, and it must STAY clean: an entry with no file
    is a guaranteed 404 on every row that matches it — the exact failure
    the KNOWN_ICONS gate exists to prevent, reintroduced from the other
    side. This is the direction a rename breaks."""
    known = _set_literal(_read("static/js/app-icons.js"), "KNOWN_ICONS")
    assert known, "KNOWN_ICONS parsed empty — the parser, not the data, is wrong"
    missing = sorted(k for k in known if k not in _icon_files())
    assert not missing, f"KNOWN_ICONS entries with no SVG on disk: {missing}"


def test_every_dark_variant_entry_has_its_dark_file():
    """`KNOWN_DARK_ICONS` rewrites `<slug>.svg` to `<slug>-dark.svg` at
    render time. A slug listed there without the `-dark` file 404s on
    dark theme only — invisible to anyone developing on light."""
    src = _read("static/js/app-icons.js")
    if "KNOWN_DARK_ICONS = new Set([" not in src:
        pytest.skip("no KNOWN_DARK_ICONS set in this build")
    files = _icon_files()
    missing = sorted(s for s in _set_literal(src, "KNOWN_DARK_ICONS")
                     if f"{s}-dark" not in files)
    assert not missing, f"dark-variant slugs with no <slug>-dark.svg: {missing}"


def _resolver_targets() -> set[str]:
    """Every slug the resolver can hand back as `/img/icons/<slug>.svg`."""
    src = _read("static/js/app-icon-resolvers.js")
    # `'key': 'slug',` in the overrides + aliases maps, skipping the
    # entries whose value is a literal path (handled separately below).
    mapped = {m.group("slug") for m in
              re.finditer(r"'[^']+':\s*'(?P<slug>[a-z0-9-]+)',", src)}
    # `['prefix-', 'slug'],` in the prefixes table.
    mapped |= {m.group("slug") for m in
               re.finditer(r"\['[a-z0-9-]+-',\s*'(?P<slug>[a-z0-9-]+)'\]", src)}
    # Literal `/img/icons/<slug>.svg` paths.
    mapped |= {m.group("slug") for m in
               re.finditer(r"/img/icons/(?P<slug>[a-z0-9-]+)\.svg", src)}
    return mapped


def test_every_resolver_target_has_a_file_on_disk():
    """An override / prefix / alias pointing at a slug with no file is
    the same 404, reached by a different route. Only targets that look
    like real slugs are checked — the maps also carry keys, and a key
    naming a brand we have no art for is fine."""
    files = _icon_files()
    known = _set_literal(_read("static/js/app-icons.js"), "KNOWN_ICONS")
    # A target is only a claim about a file when it is ALSO a declared
    # icon slug or already present; anything else is an ordinary string
    # value in those maps (a colour, a kind, a label).
    suspects = {t for t in _resolver_targets() if t in known or t in files}
    missing = sorted(t for t in suspects if t not in files)
    assert not missing, f"resolver targets with no SVG on disk: {missing}"


# --- UnifiedSSO, the case that prompted all of the above --------------


def test_unifiedsso_artwork_is_present():
    svg = _ICON_DIR / "unifiedsso.svg"
    assert svg.exists(), "the artwork itself is gone"
    body = svg.read_text(encoding="utf-8")
    # The ids are namespaced so the gradients cannot collide with another
    # icon's if the SVG is ever inlined instead of loaded through <img>.
    # A straight re-fetch from upstream reverts this silently.
    assert 'id="uss-tile"' in body, "gradient ids lost their uss- namespace"
    assert 'id="tile"' not in body, "generic gradient id is back — collision risk"


def test_the_bare_stack_name_can_resolve():
    """`unifiedsso` reaches the file only through the KNOWN_ICONS gate —
    the step whose omission left this icon invisible while the artwork
    sat on disk."""
    known = _set_literal(_read("static/js/app-icons.js"), "KNOWN_ICONS")
    assert "unifiedsso" in known, "the stack name falls through the gate again"


def test_the_namespaced_services_are_covered_by_a_prefix():
    """`unifiedsso_web` slugs to `unifiedsso-web`, which is NOT equal to
    the bare slug — so KNOWN_ICONS alone cannot carry the services and a
    prefixes entry is load-bearing, not decoration."""
    src = _read("static/js/app-icon-resolvers.js")
    prefixes = {(m.group("pfx"), m.group("slug")) for m in re.finditer(
        r"\['(?P<pfx>[a-z0-9-]+-)',\s*'(?P<slug>[a-z0-9-]+)'\]", src)}
    assert ("unifiedsso-", "unifiedsso") in prefixes, \
        "the stack's services lost their prefix entry and render blank"


@pytest.mark.parametrize("written", ["unified-sso", "unified_sso"])
def test_the_forgiving_spellings_reach_the_same_slug(written):
    """The project's rule is that a typo / spacing alias lands in BOTH
    maps, so an item name and a curated host's icon override are equally
    forgiving. One map alone is the drift this pins."""
    src = _read("static/js/app-icon-resolvers.js")
    hits = {m.group("slug") for m in re.finditer(
        rf"'{re.escape(written)}':\s*'(?P<slug>[a-z0-9-]+)'", src)}
    assert hits, f"no alias for {written!r} in either map"
    assert hits == {"unifiedsso"}, f"{written!r} points somewhere else: {hits}"
