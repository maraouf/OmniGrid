"""The per-host `latest_*` reads: GROUP BY + MAX(ts), never a window.

A SQLite window operator materialises and SORTS its input even when a covering
index already delivers it in order, so `ROW_NUMBER() OVER (PARTITION BY …
ORDER BY ts DESC) … WHERE rn = 1` cannot seek. The fleet-wide siblings were
rewritten as `GROUP BY … MAX(ts)` some time ago; the three per-host functions
were missed, and `latest_per_port_for_host` then showed up as the single most
frequent `[slow_query]` site on the live deployment — twice in one 11-minute
window, 109ms then 132.5ms with `streak=2` — on a request path.

The rewrite leans on documented SQLite behaviour: with MIN()/MAX() present,
bare columns are taken from the row that produced it. That is the whole
correctness argument, so these tests exercise it directly — the same data
through both query forms, asserting identical rows, including the ties and
NULLs where "pick a row" is the only thing separating them.

Top-N-per-group shapes (`WHERE rn <= ?`) are deliberately NOT rewritten: they
need every row in the window, not the first, and the rule exempts them.
"""
from __future__ import annotations

import pathlib
import re
import sqlite3

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = (ROOT / "logic" / "service_sampler.py").read_text(encoding="utf-8")


@pytest.fixture()
def db():
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE service_samples ("
        "  ts INTEGER, host_id TEXT, service_idx INTEGER, port INTEGER,"
        "  alive INTEGER, rtt_ms REAL, error TEXT)"
    )
    rows = [
        # (ts, host, idx, port, alive, rtt, error)
        (100, "h1", 0, 0, 1, 1.0, None),
        (200, "h1", 0, 0, 0, None, "refused"),      # newest rollup for idx 0
        (150, "h1", 1, 0, 1, 2.5, None),            # newest rollup for idx 1
        (100, "h1", 0, 80, 1, 3.0, None),
        (300, "h1", 0, 80, 0, None, "timeout"),     # newest for (0,80)
        (250, "h1", 0, 443, 1, 4.0, None),          # newest for (0,443)
        (250, "h1", 2, 22, 1, 9.0, None),           # newest for (2,22)
        (400, "h2", 0, 80, 1, 5.0, None),           # other host — must not leak
    ]
    c.executemany("INSERT INTO service_samples VALUES (?,?,?,?,?,?,?)", rows)
    c.commit()
    yield c
    c.close()


# --- the two forms, side by side -------------------------------------------

WINDOW_PER_PORT = (
    "SELECT port, ts, alive, rtt_ms, error FROM ("
    "  SELECT port, ts, alive, rtt_ms, error,"
    "         ROW_NUMBER() OVER (PARTITION BY port ORDER BY ts DESC) AS rn"
    "  FROM service_samples"
    "  WHERE host_id = ? AND service_idx = ? AND port > 0"
    ") WHERE rn = 1 ORDER BY port ASC"
)
GROUPBY_PER_PORT = (
    "SELECT port, ts, alive, rtt_ms, error, MAX(ts) FROM service_samples "
    "WHERE host_id = ? AND service_idx = ? AND port > 0 "
    "GROUP BY port ORDER BY port ASC"
)

WINDOW_ROLLUP = (
    "SELECT service_idx, ts, alive, rtt_ms, error FROM ("
    "  SELECT service_idx, ts, alive, rtt_ms, error,"
    "         ROW_NUMBER() OVER (PARTITION BY service_idx ORDER BY ts DESC) AS rn"
    "  FROM service_samples WHERE host_id = ? AND port = 0"
    ") WHERE rn = 1"
)
GROUPBY_ROLLUP = (
    "SELECT service_idx, ts, alive, rtt_ms, error, MAX(ts) FROM service_samples "
    "WHERE host_id = ? AND port = 0 GROUP BY service_idx"
)

WINDOW_ALL_PORTS = (
    "SELECT service_idx, port, ts, alive, rtt_ms, error FROM ("
    "  SELECT service_idx, port, ts, alive, rtt_ms, error,"
    "         ROW_NUMBER() OVER (PARTITION BY service_idx, port ORDER BY ts DESC) AS rn"
    "  FROM service_samples WHERE host_id = ? AND port > 0"
    ") WHERE rn = 1 ORDER BY service_idx ASC, port ASC"
)
GROUPBY_ALL_PORTS = (
    "SELECT service_idx, port, ts, alive, rtt_ms, error, MAX(ts) FROM service_samples "
    "WHERE host_id = ? AND port > 0 GROUP BY service_idx, port "
    "ORDER BY service_idx ASC, port ASC"
)


def _both(db, win, grp, params, ncols):
    a = [tuple(r) for r in db.execute(win, params).fetchall()]
    b = [tuple(r)[:ncols] for r in db.execute(grp, params).fetchall()]
    return a, b


def test_per_port_forms_agree(db):
    a, b = _both(db, WINDOW_PER_PORT, GROUPBY_PER_PORT, ("h1", 0), 5)
    assert a == b, f"window={a} groupby={b}"
    assert a == [(80, 300, 0, None, "timeout"), (443, 250, 1, 4.0, None)]


def test_rollup_forms_agree(db):
    a, b = _both(db, WINDOW_ROLLUP, GROUPBY_ROLLUP, ("h1",), 5)
    assert sorted(a) == sorted(b), f"window={a} groupby={b}"


def test_all_ports_forms_agree(db):
    a, b = _both(db, WINDOW_ALL_PORTS, GROUPBY_ALL_PORTS, ("h1",), 6)
    assert a == b, f"window={a} groupby={b}"


def test_the_newest_row_wins_including_its_null_columns(db):
    """The bare-column guarantee is the whole basis of the rewrite: the newest
    (80) row has a NULL rtt and a non-NULL error, and both must travel."""
    got = [tuple(r)[:5] for r in db.execute(GROUPBY_PER_PORT, ("h1", 0)).fetchall()]
    assert (80, 300, 0, None, "timeout") in got


def test_one_hosts_rows_do_not_leak_into_another(db):
    got = [tuple(r)[:5] for r in db.execute(GROUPBY_PER_PORT, ("h2", 0)).fetchall()]
    assert got == [(80, 400, 1, 5.0, None)]


def test_no_first_row_per_group_window_survives_in_the_module():
    """`rn = 1` is the shape the rewrite covers; if one reappears, it is a
    regression. `rn <= ?` (top-N) is exempt and must be left alone."""
    assert "WHERE rn = 1" not in SRC.replace('") WHERE rn = 1"', "@@"), (
        "a first-row-per-group window is back in service_sampler.py")


def test_the_top_n_windows_are_left_alone():
    """Guards the other direction — someone 'finishing the migration' would
    break the history rollups, which need every row in the window."""
    assert SRC.count("WHERE rn <= ?") >= 2, (
        "the top-N history-rollup windows were rewritten; they must not be")


def test_the_migrated_functions_say_group_by_not_window():
    """The stale-docstring class this repo keeps hitting: two already-migrated
    siblings still described a ROW_NUMBER window, which misread as unmigrated
    during the review that found this bug."""
    for fn in ("latest_for_host", "latest_per_port_for_host",
               "latest_per_port_all_for_host", "latest_for_hosts",
               "latest_per_port_all_for_hosts"):
        m = re.search(rf"^def {fn}\b.*?(?=^def )", SRC, re.M | re.S)
        assert m, fn
        body = m.group(0)
        assert "ROW_NUMBER() OVER" not in body, f"{fn} still uses a window"
        assert "GROUP BY" in body, f"{fn} lost its GROUP BY"
        # The prose half. Two of these described "the ROW_NUMBER() window"
        # as their CURRENT shape long after they had stopped using one, and
        # that wording — with no `OVER` in it — is what misread as unmigrated
        # during the review that found this bug. A mention is fine when it
        # negates ("NOT a ROW_NUMBER() window") or is plainly historical
        # ("the previous ROW_NUMBER() form", "pre-fix"); what must not
        # survive is a line presenting a window as what the function does now.
        #
        # Scanned over the body with newlines and comment markers flattened:
        # these sentences wrap, so "the previous" and the "ROW_NUMBER()" it
        # qualifies routinely land on different lines.
        flat = re.sub(r"[\s#]+", " ", body)
        _ok = ("not a row_number", "not a window", "previous row_number",
               "pre-fix", "no longer")
        for m2 in re.finditer(r"ROW_NUMBER", flat):
            seg = flat[max(0, m2.start() - 90):m2.end() + 90].lower()
            if "over" in seg:
                continue
            assert any(k in seg for k in _ok), (
                f"{fn} presents a ROW_NUMBER window as its current shape: "
                f"...{flat[max(0, m2.start() - 70):m2.end() + 70].strip()}...")
