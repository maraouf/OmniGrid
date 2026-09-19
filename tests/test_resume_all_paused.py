"""Resuming every paused host — `logic.host_resume.resume_all_paused`.

The web AI's "resume all paused hosts" used to report "Ran: Resume hosts" and
change nothing: the action only resumed the ticked hosts, and with none ticked
it returned early. Telegram AI had no handler for the action at all. Both now go
through this helper (the web one via `POST /api/hosts/bulk/resume` with
`all_paused`, which shares `paused_host_ids`), so these tests pin what "every
paused host" means and what one run leaves behind.
"""
from __future__ import annotations

import contextlib
import sqlite3

import pytest

from logic import events, host_resume

SCHEMA = """
CREATE TABLE host_failure_state(
  host_id TEXT, provider TEXT DEFAULT '', first_failure_ts REAL,
  consecutive_failures INT, paused INT, paused_at REAL, last_error TEXT,
  PRIMARY KEY(host_id, provider));
CREATE TABLE host_failure_events(
  ts REAL, host_id TEXT, provider TEXT, kind TEXT, error TEXT, actor TEXT);
CREATE TABLE history(
  id INTEGER PRIMARY KEY, ts REAL, op_type TEXT, target_kind TEXT,
  target_name TEXT, target_id TEXT, target_stack TEXT, status TEXT,
  duration REAL, events TEXT, error TEXT, actor TEXT);
"""

CURATED = {"ups", "asus", "nas"}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "og.db"
    seed = sqlite3.connect(path)
    seed.executescript(SCHEMA)
    seed.executemany(
        "INSERT INTO host_failure_state VALUES (?,?,0,?,?,?,?)",
        [
            ("ups", "snmp", 5, 1, 1.0, "x"),        # two paused providers
            ("ups", "http_probe", 5, 1, 1.0, "x"),
            ("asus", "", 0, 1, 1.0, "manual"),       # whole-host pause
            ("nas", "ping", 2, 0, None, None),       # failing, NOT paused
            ("gone", "snmp", 5, 1, 1.0, "x"),        # paused but no longer curated
        ],
    )
    seed.commit()
    seed.close()

    @contextlib.contextmanager
    def _conn():
        c = sqlite3.connect(path)
        try:
            yield c
            c.commit()
        finally:
            c.close()

    monkeypatch.setattr(host_resume, "db_conn", _conn)
    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(events, "publish",
                        lambda kind, payload, **_: published.append((kind, payload)))
    reader = sqlite3.connect(path)
    yield reader, published
    reader.close()


def test_paused_means_any_paused_row_on_a_curated_host(db):
    assert sorted(host_resume.paused_host_ids(CURATED)) == ["asus", "ups"]


def test_resume_all_clears_every_layer_and_leaves_the_rest(db):
    reader, published = db
    result = host_resume.resume_all_paused(CURATED, actor="telegram-ai:op")

    assert result["ok"] is True
    assert sorted(result["resumed"]) == ["asus", "ups"]
    # Both of ups' provider rows and asus' whole-host row are gone; the
    # unpaused failure streak and the uncurated host are untouched.
    assert sorted(reader.execute(
        "SELECT host_id, provider FROM host_failure_state").fetchall()) == [
        ("gone", "snmp"), ("nas", "ping")]
    # One timeline entry per cleared row, one history row per host.
    assert sorted(reader.execute(
        "SELECT host_id, provider, kind FROM host_failure_events").fetchall()) == [
        ("asus", "", "recovered"), ("ups", "http_probe", "recovered"),
        ("ups", "snmp", "recovered")]
    assert sorted(reader.execute(
        "SELECT op_type, target_id, actor FROM history").fetchall()) == [
        ("hosts_bulk_resume", "asus", "telegram-ai:op"),
        ("hosts_bulk_resume", "ups", "telegram-ai:op")]
    # ONE bulk event, not one per host.
    assert len(published) == 1
    kind, payload = published[0]
    assert kind == "host:bulk_action_applied"
    assert payload["action"] == "resume"
    assert sorted(payload["host_ids"]) == ["asus", "ups"]


def test_nothing_paused_is_a_clean_no_op(db):
    reader, published = db
    host_resume.resume_all_paused(CURATED, actor="t")
    published.clear()

    again = host_resume.resume_all_paused(CURATED, actor="t")
    assert again == {"ok": True, "resumed": [], "error": ""}
    assert published == []
    assert reader.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 2
