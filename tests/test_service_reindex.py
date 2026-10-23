"""Deleting an app chip must not hand its probe history to the chip after it.

Samples are keyed on the chip's POSITION. Removing a chip renumbers the ones
behind it, and a healthy Veeam Agent that slid into the slot a removed Readarr
vacated was counted as down on the Apps badge, wearing Readarr's `ConnectError`.
"""
from __future__ import annotations

import sqlite3

import pytest

from logic.service_reindex import chip_keyed_tables, drop_and_shift


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    # Same shape as service_samples: the position is part of the primary key.
    c.execute("CREATE TABLE service_samples (ts INTEGER, host_id TEXT, service_idx INTEGER,"
              " port INTEGER, alive INTEGER, PRIMARY KEY (ts, host_id, service_idx, port))")
    # And a second per-app table with a different key, to prove tables are found, not listed.
    c.execute("CREATE TABLE flaresolverr_sessions (host_id TEXT, service_idx INTEGER,"
              " session_id TEXT, PRIMARY KEY (host_id, service_idx, session_id))")
    # Neither of these belongs to a chip.
    c.execute("CREATE TABLE ping_samples (ts INTEGER, host_id TEXT, PRIMARY KEY (ts, host_id))")
    c.execute("CREATE TABLE odd (service_idx INTEGER, value TEXT)")
    yield c
    c.close()


def _seed(c, host, idxs, ts=100):
    for i in idxs:
        # alive=i so each chip's rows are recognisable after the move
        c.execute("INSERT INTO service_samples VALUES (?,?,?,?,?)", (ts, host, i, 0, i))


def _by_idx(c, host):
    return {r[0]: r[1] for r in c.execute(
        "SELECT service_idx, alive FROM service_samples WHERE host_id = ? ORDER BY service_idx", (host,))}


def test_only_tables_keyed_by_a_chip_are_found(conn):
    assert chip_keyed_tables(conn) == ["flaresolverr_sessions", "service_samples"]


def test_later_chips_take_their_history_with_them(conn):
    _seed(conn, "torrents", [0, 1, 2, 3])
    drop_and_shift(conn, "torrents", 1)
    # chip 1's rows are gone; chips 2 and 3 are now 1 and 2 and still carry THEIR rows
    assert _by_idx(conn, "torrents") == {0: 0, 1: 2, 2: 3}


def test_neighbours_sharing_a_timestamp_do_not_collide_on_the_primary_key(conn):
    # every chip sampled at the same instant — the realistic case, and the one a
    # plain `service_idx - 1` breaks on
    _seed(conn, "docker", list(range(12)), ts=500)
    drop_and_shift(conn, "docker", 2)
    got = _by_idx(conn, "docker")
    assert sorted(got) == list(range(11))
    assert got[0] == 0 and got[1] == 1
    assert [got[i] for i in range(2, 11)] == list(range(3, 12))


def test_another_hosts_chips_are_untouched(conn):
    _seed(conn, "docker", [0, 1, 2])
    _seed(conn, "torrents", [0, 1, 2])
    drop_and_shift(conn, "docker", 0)
    assert _by_idx(conn, "torrents") == {0: 0, 1: 1, 2: 2}


def test_removing_the_last_chip_only_deletes_its_rows(conn):
    _seed(conn, "docker", [0, 1, 2])
    changed = drop_and_shift(conn, "docker", 2)
    assert _by_idx(conn, "docker") == {0: 0, 1: 1}
    assert changed == {"service_samples": (1, 0)}


def test_a_second_per_app_table_is_shifted_too(conn):
    for i, sid in ((0, "a"), (1, "b"), (2, "c")):
        conn.execute("INSERT INTO flaresolverr_sessions VALUES ('h', ?, ?)", (i, sid))
    drop_and_shift(conn, "h", 0)
    rows = conn.execute("SELECT service_idx, session_id FROM flaresolverr_sessions ORDER BY 1").fetchall()
    assert rows == [(0, "b"), (1, "c")]


def test_unrelated_tables_are_left_alone(conn):
    conn.execute("INSERT INTO ping_samples VALUES (1, 'docker')")
    conn.execute("INSERT INTO odd VALUES (3, 'keep')")
    drop_and_shift(conn, "docker", 0)
    assert conn.execute("SELECT COUNT(*) FROM ping_samples").fetchone()[0] == 1
    assert conn.execute("SELECT service_idx FROM odd").fetchone()[0] == 3
