"""The banded chart scale, and the three unit labels that were missing.

Both came out of one operator screenshot of the APC drawer.

**The band.** Input voltage was zero-floored, so a 200-231 V series drew as a
straight line pinned to the top of the plot — the axis was honest (the builder
really did divide by max) and the chart still showed nothing. Flooring at the
series minimum instead would have been the opposite lie: a supply wandering
228-231 V would fill the whole height and read as violent instability. The band
takes the data's own range when it is wide enough to be worth seeing, and a
fixed window centred on the data when it is not.

**The units.** The runtime axis read `77` / `0` while its own caption two
centimetres away read `low 57m`; Prowlarr's response-time and failure-rate axes
were bare while the text around them stated milliseconds and percent. An axis
that names a quantity without naming what it is measured in is doing about half
its job.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = ROOT / "static" / "_partials" / "_components" / "apps"
CHARTS = (ROOT / "static" / "js" / "app-charts.js").read_text(encoding="utf-8")
APC_JS = (ROOT / "static" / "js" / "apps" / "apc.js").read_text(encoding="utf-8")


# --- a faithful port of _ogChartBand, exercised over the cases that matter ---

def band(series, min_span):
    vals = [float(v) for v in series]
    lo, hi = min(vals), max(vals)
    rng = hi - lo
    if rng >= min_span:
        return lo, (hi if hi > lo else lo + 1)
    pad = (min_span - rng) / 2
    return lo - pad, hi + pad


def test_a_wide_series_keeps_its_own_range():
    """The screenshot's case: 31 V of real movement is wider than the floor
    span, so the plot is the data and nothing is invented."""
    lo, hi = band([200, 231, 214, 208], 20)
    assert (lo, hi) == (200.0, 231.0)


def test_a_tight_series_is_not_magnified():
    """The failure mode plain minmax would have introduced. Three volts of
    drift must not fill the plot — it must look like three volts."""
    lo, hi = band([228, 231, 229, 230], 20)
    assert hi - lo == 20.0, "band collapsed to the data's range"
    assert lo < 228 and hi > 231, "the data should sit inside the band"
    # and it should sit in the MIDDLE of it, not against an edge
    assert abs(((lo + hi) / 2) - 229.5) < 0.001


def test_a_dead_flat_series_still_has_height():
    """A divisor of zero would put every point at NaN and draw nothing."""
    lo, hi = band([230, 230, 230], 20)
    assert hi > lo


def test_a_flat_series_with_no_span_still_has_height():
    """Same guard on the other path — range >= span with range == 0."""
    lo, hi = band([230, 230], 0)
    assert hi > lo


def test_the_band_is_wide_enough_to_be_quiet_but_narrow_enough_to_show_a_sag():
    """The span is a judgement call, so pin what it has to achieve: ordinary
    wander stays small, while a real brownout still breaks out clearly."""
    lo, hi = band([229, 230, 231], 20)
    frac = (231 - 229) / (hi - lo)
    assert frac < 0.2, f"2V of wander occupies {frac:.0%} of the plot — too loud"
    lo2, hi2 = band([230, 230, 205], 20)   # a genuine sag
    frac2 = (230 - 205) / (hi2 - lo2)
    assert frac2 > 0.8, f"a 25V sag occupies only {frac2:.0%} — too quiet"


# --- the axis and the line must agree ---------------------------------------

def test_the_axis_helper_understands_the_band_mode():
    assert "_ogChartBand(series, minSpan)" in CHARTS, "the band helper is gone"
    assert "mode === 'band'" in CHARTS, "ogChartAxis does not handle band mode"


def test_the_axis_and_the_builder_call_the_same_helper():
    """The whole correctness argument. If the axis computed its own edges the
    labels could describe a plot the line was never drawn into."""
    axis_seg = CHARTS[CHARTS.index("mode === 'band'"):]
    axis_seg = axis_seg[:axis_seg.index("}")]
    assert "_ogChartBand(arr, o.minSpan)" in axis_seg
    assert "_ogChartBand(series, APC_VOLTAGE_MIN_SPAN_V)" in APC_JS


def test_the_span_is_not_written_twice():
    """A number repeated in the markup and the module is a number that will
    drift. The partial asks the module for it."""
    body = (APPS / "apc_extras.html").read_text(encoding="utf-8")
    assert "minSpan: apcVoltageMinSpan()" in body
    assert "minSpan: 20" not in body, "the literal is back in the markup"


def test_voltage_has_its_own_builder_not_a_branch():
    """One builder, one scaling family — the static mode-vs-builder check
    reads a function's `y =` line, and cannot do that for a function that
    scales two different ways depending on an argument."""
    assert "function apcVoltagePoints(inst)" in APC_JS
    spark = APC_JS[APC_JS.index("function apcSparkPoints"):APC_JS.index("function apcVoltagePoints")]
    assert "_ogChartBand" not in spark, (
        "apcSparkPoints now scales two ways; split it instead")
    assert "band.floor" not in spark


def test_the_zero_floored_apc_charts_stay_zero_floored():
    """Battery, load and runtime keep the zero floor deliberately: zero means
    something in all three, and for runtime it is the entire point."""
    body = (APPS / "apc_extras.html").read_text(encoding="utf-8")
    for key in ("'battery'", "'load'", "'runtime'"):
        assert f"apcSparkPoints(inst, {key})" in body, f"{key} changed builder"


# --- the units --------------------------------------------------------------

def test_the_three_unit_bearing_axes_name_their_unit():
    cases = [
        ("apc_extras.html", "runtime_series", "'m'"),
        ("prowlarr_extras.html", "series_response_ms", "'ms'"),
        ("prowlarr_extras.html", "series_fail_rate", "'%'"),
        ("apc_extras.html", "voltage_series", "'V'"),
    ]
    for fname, series, unit in cases:
        body = (APPS / fname).read_text(encoding="utf-8")
        pattern = r"ogChartAxis\([^\n]*?{}[^\n]*?\)\s*\}}".format(re.escape(series))
        m = re.search(pattern, body)
        assert m, f"{fname}: no axis found for {series}"
        assert f"unit: {unit}" in m.group(0), (
            f"{fname}: the {series} axis does not say {unit} — it labels a "
            f"quantity without saying what it is measured in")
