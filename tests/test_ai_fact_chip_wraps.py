"""Both tool-fact chips wrap; neither runs off the side of the sidebar.

`aiToolFacts` writes two kinds of chip into an assistant turn — `--fact` for
something a tool established, `--failed` for a tool that did not. Both carry a
SENTENCE: a MAC, a host, an interface, and for the failed one the device's own
error text quoted back.

The base `.ai-bubble-action` is built for a two-word outcome ("Ran"), so it
pins `white-space: nowrap` and a fixed 16px height. The wrap override was
written onto `--fact` alone, which left `--failed` inheriting the base — and
`--failed` carries the LONGEST text of the two, because it quotes the switch.
It ran off the right edge of the sidebar. The bubble is `width: max-content`
under a percentage `max-width`, so an unwrappable chip has nowhere to go but
out past it.

These tests pin the pairing rather than the individual rule, because the defect
was not a missing property — it was one variant styled and its twin forgotten.
"""
from __future__ import annotations

import pathlib
import re

_RAW = (pathlib.Path(__file__).resolve().parents[1] / "static" / "css" / "style.css").read_text(encoding="utf-8")
# Comments stripped before any parsing: several of these rules carry a comment
# INSIDE the block, and a naive split reads `/* width` as a property name.
CSS = re.sub(r"/\*.*?\*/", "", _RAW, flags=re.S)


def _rule_bodies(selector: str) -> list[str]:
    """Every rule block whose selector list mentions `selector`."""
    out = []
    for m in re.finditer(r"(?P<sel>[^{}]+)\{(?P<body>[^{}]*)\}", CSS):
        sel = m.group("sel")
        pattern = r"{}\s*(?:,|\{{|$|\s)".format(re.escape(selector))
        if re.search(pattern, sel):
            out.append(m.group("body"))
    return out


def _props_for(selector: str) -> dict:
    props = {}
    for body in _rule_bodies(selector):
        for line in body.split(";"):
            if ":" in line:
                k, _, v = line.partition(":")
                props[k.strip()] = v.strip()
    return props


def test_the_base_chip_still_refuses_to_wrap():
    """Not a bug — a two-word outcome chip should stay on one line. It is the
    reason the variants need an override at all, so if this ever changes the
    overrides below become redundant rather than load-bearing."""
    base = _props_for(".ai-bubble-action")
    assert base.get("white-space") == "nowrap"


def test_both_fact_variants_are_allowed_to_wrap():
    """The actual regression: only one of the two had this."""
    for sel in (".ai-bubble-action--fact", ".ai-bubble-action--failed"):
        p = _props_for(sel)
        assert p.get("white-space") == "normal", f"{sel} still inherits nowrap"


def test_both_lose_the_fixed_height_that_would_clip_a_second_line():
    for sel in (".ai-bubble-action--fact", ".ai-bubble-action--failed"):
        p = _props_for(sel)
        assert p.get("height") == "auto", f"{sel} keeps a fixed height"
        assert "min-height" in p, f"{sel} lost its minimum height"


def test_both_can_break_a_mac_or_an_fqdn():
    """Wrapping alone is not enough — `dc:a6:32:b4:4d:73` and
    `switch52mp01.example.com` offer no break opportunity, so one long token
    would still push the box wider than the bubble."""
    for sel in (".ai-bubble-action--fact", ".ai-bubble-action--failed"):
        p = _props_for(sel)
        assert p.get("overflow-wrap") == "anywhere", f"{sel} cannot break a long token"


def test_the_shared_rule_does_not_repaint_the_failed_chip():
    """The wrap properties are shared, the colours are not. A colour in the
    shared block would win over `--failed`'s red, because it comes later —
    turning every failure chip the informational blue."""
    shared = None
    for m in re.finditer(r"(?P<sel>[^{}]+)\{(?P<body>[^{}]*)\}", CSS):
        sel = m.group("sel")
        if "--fact" in sel and "--failed" in sel and "svg" not in sel:
            shared = m.group("body")
            break
    assert shared is not None, "the shared wrap rule is gone"
    assert "color" not in shared, (
        "the shared rule sets a colour; it is declared after --failed, so it "
        "would repaint the red failure chip")


def test_the_icon_is_pinned_and_cannot_be_squeezed():
    """A 10px icon centred against a three-line block floats oddly, and a
    flex item with no `flex: none` gets compressed by the text beside it."""
    icon = _props_for(".ai-bubble-action--failed > svg")
    assert icon.get("flex") == "none"
    assert "margin-top" in icon


def test_the_bubble_that_holds_them_is_still_width_capped():
    """Context for why wrapping matters here: the bubble sizes to its widest
    content and is capped at 80%. Remove the cap and an unwrappable chip would
    simply stretch the bubble instead of overflowing — a different bug."""
    bubble = _props_for(".ai-bubble")
    assert bubble.get("width") == "max-content"
    assert bubble.get("max-width", "").endswith("%")
