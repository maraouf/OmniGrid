"""Tests for the shared app-drawer chart axes, and the include loop they hit.

The charts in the app drawer drew a shape and nothing else — no scale, no
baseline, no time span — so a rising line told you something grew but not from
what, to what, or over how long. `og-chart-axes.html` + `ogChartAxis()` add
that, once, for every app rather than per app.

The second half of this file exists because adding that partial introduced a
real defect. Its header comment documented its own call site, and
`_expand_includes` is a plain regex over the whole file with NO awareness of
comments — so the example marker was a genuine edge in the include graph and
the partial included ITSELF. The expander looped to its depth cap, printed one
WARN line, and then STRIPPED every marker still unresolved: markup silently
dropped out of the assembled page, which grew by ~88 KB of duplicated content
on the way there. The only visible symptom was a single log line.
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
PARTIALS = ROOT / "static" / "_partials"
INCLUDE_RE = re.compile(r"<!--\s*INCLUDE:\s*(?P<target>[^>]+?)\s*-->")


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


def _all_partials() -> list[pathlib.Path]:
    return sorted(PARTIALS.rglob("*.html"))


def _builder_scaling() -> dict[str, str]:
    """Which FLOOR each per-app SVG path builder scales against, read out of the
    builders themselves rather than from a table that could drift.

    Three families disagree: `v / max` puts the floor at zero, `(v - min) /
    range` puts it at the series MINIMUM, and `v / 100` fixes the ceiling at
    100. Both the axis-mode check and the shared-scale check below depend on
    knowing which is which.
    """
    scaling: dict[str, str] = {}
    fn_re = re.compile(r"function (?P<name>\w+)\s*\(")
    for js in (ROOT / "static" / "js" / "apps").glob("*.js"):
        text = _read(js)
        for m in fn_re.finditer(text):
            body = text[m.end():m.end() + 1600]
            cut = body.find("\nfunction ")
            if cut > 0:
                body = body[:cut]
            if re.search(r"-\s*min\)\s*/\s*range", body):
                scaling[m.group("name")] = "minmax"
            elif re.search(r"/\s*100\s*\)?\s*\*\s*(?:H|height)", body):
                scaling[m.group("name")] = "fixed"
            elif re.search(r"/\s*(?:max|top|m)\)?\s*\*\s*(?:H|height)", body):
                scaling[m.group("name")] = "zero"
    return scaling


def test_no_partial_includes_itself():
    """The defect this shipped with. A partial naming itself loops the
    expander to its cap, and the cap's response is to DELETE the markers it
    could not resolve — so the page loses content and stays quiet about it."""
    offenders = []
    for p in _all_partials():
        own = p.relative_to(PARTIALS).as_posix()
        for target in INCLUDE_RE.findall(_read(p)):
            if target.strip() == own:
                offenders.append(own)
    assert not offenders, (
        f"partial(s) include themselves: {offenders} — the include expander is "
        f"not comment-aware, so even an example marker inside a comment counts")


def test_the_include_graph_has_no_cycle_at_all():
    """Broader than the self-reference case: any loop hits the same cap and
    the same silent strip."""
    def kids(rel: str) -> list[str]:
        p = PARTIALS / rel
        if not p.exists():
            return []
        return [k.strip() for k in INCLUDE_RE.findall(_read(p))]

    cycles: list[str] = []

    def walk(rel: str, chain: list[str]) -> None:
        if rel in chain:
            cycles.append(" -> ".join(chain[chain.index(rel):] + [rel]))
            return
        for k in kids(rel):
            walk(k, chain + [rel])

    shell = _read(ROOT / "static" / "index.html")
    for top in [k.strip() for k in INCLUDE_RE.findall(shell)]:
        walk(top, [])
    assert not cycles, f"include cycle(s): {sorted(set(cycles))}"


def test_the_shared_axes_partial_exists_and_is_generic():
    p = PARTIALS / "_components" / "og-chart-axes.html"
    assert p.exists(), "the shared chart-axes partial is gone"
    body = _read(p)
    # It must not know which app it is drawing for — the whole point is one
    # component for every chart.
    assert "opnsense" not in body.lower(), (
        "the shared axes partial named a specific app — it is meant to be "
        "consumed with an `axis()` expression, not branched per app")
    for cls in ("og-chart-y--max", "og-chart-y--min",
                "og-chart-x--start", "og-chart-x--end"):
        assert cls in body, f"{cls} missing — that edge of the scale is unlabelled"


def test_the_axes_are_hidden_when_there_is_nothing_to_plot():
    """An axis drawn around an empty chart claims a scale that no data
    supports."""
    body = _read(PARTIALS / "_components" / "og-chart-axes.html")
    assert 'x-if="axis().has"' in body


def test_the_axis_helper_reports_the_series_range():
    """The floor is zero for these charts because that is what every per-app
    path builder scales against; the ceiling is the series max."""
    js = _read(ROOT / "static" / "js" / "app-charts.js")
    assert "ogChartAxis(series, opts)" in js, "the shared axis helper is gone"
    assert "_ogChartNum" in js, "the compact axis-number formatter is gone"
    body = js[js.index("ogChartAxis(series, opts)"):]
    body = body[:body.index("\n  _ogChartNum")]
    assert "maxLabel" in body and "minLabel" in body
    assert "startLabel" in body and "endLabel" in body


def test_the_chart_gridlines_are_css_not_svg_elements():
    """Deliberate: adding elements inside 46 chart <svg>s to draw two lines
    risks the documented failure where an Alpine template inside an <svg>
    blanks the entire page. A border renders the same."""
    css = _read(ROOT / "static" / "css" / "style.css")
    assert ".og-chart-plot {" in css
    seg = css[css.index(".og-chart-plot {"):]
    seg = seg[:seg.index("}")]
    assert "border-top" in seg and "border-bottom" in seg


def test_every_axis_label_string_is_translatable():
    bundle = json.loads(_read(ROOT / "static" / "i18n" / "en.json"))
    assert bundle["apps"]["charts"]["now"], "apps.charts.now missing"
    assert "{days}" in bundle["apps"]["opnsense"]["span_days"]


def test_the_axis_mode_matches_how_each_chart_actually_scales():
    """The defect this nearly shipped with.

    The path builders belong to three families that disagree about the FLOOR
    of the plot: `v / max` puts it at zero, `(v - min) / range` puts it at the
    series MINIMUM, and `v / 100` fixes the ceiling at 100. Labelling a
    minmax chart's floor "0" states something the drawing does not — on a disk
    series that never falls below 4 TB it invents a floor the line never
    approaches.

    So every wrapped chart must pass the mode belonging to ITS builder. This
    reads the builder's own scaling expression out of the JS and checks the
    markup against it, rather than trusting a table that could drift.
    """
    scaling = _builder_scaling()

    wrapper_re = re.compile(
        r"ogChartAxis\((?P<args>[^\n]*?)\)\s*\}\"[\s\S]{0,400}?:d=\"(?P<fn>\w+)\("
        r"|ogChartAxis\((?P<args2>[^\n]*?)\)\s*\}\"[\s\S]{0,400}?:points=\"(?P<fn2>\w+)\(")
    problems = []
    for p in sorted((PARTIALS / "_components" / "apps").glob("*_extras.html")):
        body = _read(p)
        for m in wrapper_re.finditer(body):
            fn = m.group("fn") or m.group("fn2")
            args = m.group("args") or m.group("args2") or ""
            expected = scaling.get(fn)
            if not expected:
                continue
            declared = "zero"
            mm = re.search(r"mode:\s*'(?P<m>\w+)'", args)
            if mm:
                declared = mm.group("m")
            # `fixed` + an explicit `fixedMax` is a ZERO floor with a ceiling
            # supplied from outside — which is what a builder like
            # `tautulliStreamTypePath(series, sharedMax)` draws: it divides by
            # a max handed to it so two lines share one scale. That is
            # compatible with a zero-floor builder, and is NOT the same thing
            # as bare `fixed` (ceiling pinned at 100 by the builder itself).
            if declared == "fixed" and "fixedMax:" in args and expected == "zero":
                continue
            if declared != expected:
                problems.append(f"{p.name}: {fn} scales '{expected}' but axis says '{declared}'")
    assert not problems, "axis mode disagrees with the chart's own scaling:\n  " + "\n  ".join(problems)


def test_the_opnsense_charts_are_wired_to_the_shared_component():
    """The reported case. Each of its four charts should carry a scale."""
    body = _read(PARTIALS / "_components" / "apps" / "opnsense_extras.html")
    assert body.count('class="og-chart') == 8, (
        "expected 4 chart wrappers + 4 plot wrappers on the OPNsense drawer")
    assert body.count("og-chart-axes.html") == 4
    # The units differ per chart and must not be copy-pasted from one another.
    for unit in ("'B/s'", "'%'", "'°C'"):
        assert unit in body, f"chart unit {unit} missing — a scale without a unit"


def test_the_existing_chart_svgs_were_not_modified():
    """The wrapper approach is what keeps this safe to roll out across 46
    charts: the series, the path helper and the viewBox are untouched."""
    body = _read(PARTIALS / "_components" / "apps" / "opnsense_extras.html")
    assert body.count('viewBox="0 0 100 24"') == 4
    assert body.count("opnsenseUsagePath(") == 4
def test_sibling_lines_in_one_frame_are_not_each_normalised_to_their_own_range():
    r"""A frame drawing several lines through the SAME builder, each called with
    only its own series, normalises every line to its own min/max — so a series
    a fifth the size of its neighbour still climbs to the top of the plot and
    the two read as level. Two same-unit lines in one frame have to share one
    scale, or the picture says something the numbers do not.

    A builder that pins to a FIXED range (`v / 100`) is exempt: every line is
    already on the same scale, which is exactly why Proxmox can draw three
    percentages through a single-argument builder and stay honest.
    """
    import collections

    svg_re = re.compile(r"<svg\b.*?</svg>", re.S)
    call_re = re.compile(r'(?::points|:d)="\s*(?P<fn>\w+)\((?P<args>[^"]*?)\)\s*"')

    fixed_scale = {n for n, mode in _builder_scaling().items() if mode == "fixed"}

    problems = []
    for p in sorted((PARTIALS / "_components" / "apps").glob("*_extras.html")):
        body = _read(p)
        for m in svg_re.finditer(body):
            calls = call_re.findall(m.group(0))
            for fn, count in collections.Counter(f for f, _ in calls).items():
                if count < 2 or fn in fixed_scale:
                    continue
                args = [a for f, a in calls if f == fn]
                # Every call passing ONE argument means no sibling series and no
                # shared ceiling ever reached the builder.
                if all("," not in a for a in args):
                    line = body.count("\n", 0, m.start()) + 1
                    problems.append(
                        f"{p.name}:{line} {fn} draws {count} lines, each on its own scale")
    assert not problems, (
        "sibling lines normalised independently:\n  " + "\n  ".join(problems))


def test_the_long_horizon_speedtest_trend_carries_a_dual_axis():
    """Mbps and ms in one frame is not a reason to leave the frame unlabelled --
    it is a reason to draw two y-axes, which the recent-series chart in this
    same app already did. This one drew three unlabelled lines instead.
    """
    body = _read(PARTIALS / "_components" / "apps" / "speedtest_tracker_extras.html")
    assert body.count("speedtestTrendChartModel(") == 1, (
        "the long-horizon trend chart is not wired to the dual-axis model")
    # Both axed charts: Mbps ticks on the left, ms ticks on the right, time on x.
    assert body.count("yTicks[2].label") == 2
    assert body.count("pTicks[1].label") == 2
    assert body.count("xTicks[1].label") == 2


def test_download_and_upload_share_one_mbps_scale_while_ping_gets_its_own():
    """The whole point of the dual axis: the two Mbps series are comparable to
    each other, and the millisecond series is not forced onto their scale."""
    js = _read(ROOT / "static" / "js" / "apps" / "speedtest_tracker.js")
    assert "function _axedModel(" in js, "the shared axed-model builder is gone"
    body = js[js.index("function _axedModel("):]
    body = body[:body.index("// Build the AXED chart model")]
    # One ceiling covering BOTH Mbps series; a separate one for ping.
    assert "peak(dl), peak(ul)" in body, "download and upload no longer share a ceiling"
    assert "pingMax = _niceMax(Math.max(1, peak(pg)))" in body, "ping lost its own scale"
    assert "path(dl, yMbps)" in body and "path(ul, yMbps)" in body
    assert "path(pg, yPing)" in body


def test_the_replaced_single_series_trend_builder_is_gone():
    """Leaving it exported invites the next chart to reach for it."""
    js = _read(ROOT / "static" / "js" / "apps" / "speedtest_tracker.js")
    assert "speedtestTrendSparkPath" not in js
    body = _read(PARTIALS / "_components" / "apps" / "speedtest_tracker_extras.html")
    assert "speedtestTrendSparkPath" not in body
