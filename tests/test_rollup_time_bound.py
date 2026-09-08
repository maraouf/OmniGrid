"""The Apps sparkline rollup reads the window it needs, not the whole retention.

`history_rollup_all_for_hosts` was the slowest query on the deployment — 3.8
seconds in one sample. A ROW_NUMBER() window sorts everything handed to it
before the `rn <= 24` cap can discard any of it, and nothing bounded what was
handed to it, so drawing a sparkline covering the last two hours meant sorting
seven days. At the defaults that is roughly eighty-four times more rows than
the chart can display.

The bound is DERIVED (`max_points * interval * safety`), not a fixed number of
days, and that is the part worth protecting. Raise the probe interval to an
hour and twenty-four points genuinely spans a day; a hardcoded 24h window would
then start silently truncating charts. Computed, the window grows with the
interval, and once it reaches retention it stops binding entirely — so the
change cannot cost data at any interval setting.

Measured on 967,680 rows (40 hosts x 6 chips x 7 days, plus per-port noise):
521ms before, 115ms after, results identical.
"""
from __future__ import annotations

import sqlite3
import time
from unittest import mock

import pytest

import logic.service_sampler as ss
from logic.tuning import Tunable as T


def _cutoff(retention_days, interval_s, points=24):
    def fake_int(key):
        return retention_days if key == T.STATS_HISTORY_DAYS else 0
    with mock.patch.object(ss.tuning, "tuning_int", side_effect=fake_int), \
         mock.patch.object(ss, "_resolve_service_probe_interval", return_value=interval_s):
        return ss._rollup_cutoff_ts(points)


# --- the derived window -----------------------------------------------------

def test_the_default_window_is_far_wider_than_the_chart_needs():
    """24 points at 300s is two hours of data; the bound gives a day."""
    cut = _cutoff(7, 300)
    hours = (int(time.time()) - cut) / 3600
    assert 23.5 < hours < 24.5, f"expected ~24h of headroom, got {hours:.1f}h"


def test_a_longer_probe_interval_widens_the_window_rather_than_truncating():
    """The reason the bound is derived. At a 1h interval, 24 points spans a
    day and needs twelve days of headroom — more than the 7-day retention —
    so the bound correctly stops binding instead of cutting the chart short.
    A hardcoded 24h window would have silently truncated here."""
    assert _cutoff(7, 3600) == 0


def test_a_longer_retention_lets_the_wider_window_apply():
    """Same 1h interval, but 90 days of retention — now the derived window
    fits inside it and the bound engages again."""
    cut = _cutoff(90, 3600)
    assert cut > 0
    hours = (int(time.time()) - cut) / 3600
    assert 287 < hours < 289, f"expected ~288h, got {hours:.1f}h"


def test_a_window_that_reaches_retention_does_not_bind():
    """No point filtering on ts when the filter would keep everything —
    behaviour returns to exactly what it was before."""
    assert _cutoff(1, 300) == 0


def test_unresolvable_tunables_scan_everything_rather_than_guess():
    """A bad interval must not produce a cutoff that quietly hides data. The
    safe direction here is the SLOW one."""
    assert _cutoff(7, 0) == 0
    with mock.patch.object(ss.tuning, "tuning_int", side_effect=RuntimeError("boom")):
        assert ss._rollup_cutoff_ts(24) == 0


# --- the query itself -------------------------------------------------------

WINDOW = ("SELECT host_id, service_idx, ts, alive FROM ("
          "  SELECT host_id, service_idx, ts, alive,"
          "   ROW_NUMBER() OVER (PARTITION BY host_id, service_idx ORDER BY ts DESC) AS rn"
          "  FROM service_samples WHERE host_id IN (?) AND port = 0{extra}"
          ") WHERE rn <= ? ORDER BY host_id ASC, service_idx ASC, ts ASC")


@pytest.fixture()
def db():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE service_samples (ts INTEGER, host_id TEXT, service_idx INTEGER,"
              " port INTEGER, alive INTEGER, rtt_ms REAL, error TEXT)")
    now = 1_800_000_000
    rows = []
    for k in range(600):                      # ~2 days at 300s
        rows.append((now - k * 300, "h1", 0, 0, 1, 1.0, None))
        rows.append((now - k * 300, "h1", 0, 443, 1, 1.0, None))
    c.executemany("INSERT INTO service_samples VALUES (?,?,?,?,?,?,?)", rows)
    c.commit()
    yield c, now
    c.close()


def test_the_bound_does_not_change_what_the_chart_draws(db):
    """The whole point: same 24 newest points either way."""
    c, now = db
    a = c.execute(WINDOW.format(extra=""), ("h1", 24)).fetchall()
    b = c.execute(WINDOW.format(extra=" AND ts >= ?"), ("h1", now - 86400, 24)).fetchall()
    assert a == b
    assert len(a) == 24


def test_a_chip_silent_longer_than_the_window_goes_quiet(db):
    """The accepted cost, asserted rather than left implicit. A chip whose
    last sample predates the window returns nothing — and the card states
    that separately via `last_probe`, which this query does not feed.

    Note this is NOT the same as a chip that is DOWN: a failed probe still
    writes a row (alive=0), so a down chip keeps a dense series. Only a chip
    that stopped being probed at all falls out.
    """
    c, now = db
    dormant = now - (5 * 86400)
    c.execute("INSERT INTO service_samples VALUES (?,?,?,?,?,?,?)",
              (dormant, "h2", 0, 0, 1, 1.0, None))
    c.commit()
    unbounded = c.execute(WINDOW.format(extra=""), ("h2", 24)).fetchall()
    bounded = c.execute(WINDOW.format(extra=" AND ts >= ?"), ("h2", now - 86400, 24)).fetchall()
    assert len(unbounded) == 1, "the dormant sample should exist unbounded"
    assert bounded == [], "the dormant sample should fall outside the window"


def test_a_down_chip_keeps_its_series(db):
    """Guards the distinction the cost above turns on — if a failed probe ever
    stopped writing a row, this bound would start hiding live failures."""
    import inspect
    src = inspect.getsource(ss._persist_row)
    assert "1 if alive else 0" in src, (
        "_persist_row no longer records failed probes as rows; the time bound "
        "would then hide a chip that is actively failing, not just a dormant one")


def test_both_rollups_carry_the_bound():
    """The single-host sibling has the same shape and the same problem."""
    import inspect
    for fn in (ss.history_rollup_all_for_host, ss.history_rollup_all_for_hosts):
        src = inspect.getsource(fn)
        assert "_rollup_cutoff_ts" in src, f"{fn.__name__} is unbounded"
        assert "ts >= ?" in src, f"{fn.__name__} has no ts predicate"
