"""The EFS-aggregate diagnostic: it must name its host, and it must be quiet.

Read from the live deployment's own logs: 360 of 360 occurrences printed
``[beszel] efs-aggregate ?:`` — the hostname was a literal question mark in
every one — and those 360 lines were 18% of an 11-minute sample.

Both halves matter and they are the same defect twice. The host key is not on
``info``, so the line could not say which host it described; and because it
printed unconditionally per host per tick it drowned the log while doing so.
The probe-entry diagnostic that used to sit a few lines above was deleted for
precisely this pair of reasons, and the comment recording that deletion sat
directly over a line that had inherited both.
"""
from __future__ import annotations

import io
import contextlib

from logic import beszel


def _probe(host_key: str = "", *, total_gib: float = 48.0, used_gib: float = 35.2):
    """Run extract_stats over a record shaped like one with EXTRA_FILESYSTEMS
    configured, returning whatever it printed."""
    info = {"k": "6.1.0", "c": 4}
    stats = {
        "m": 16.0, "mu": 8.0, "d": 10.0, "du": 5.0,
        "efs": {"/mnt/data": {"d": total_gib, "du": used_gib}},
    }
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        beszel.extract_stats(info, stats, host_key=host_key)
    return buf.getvalue()


def _reset():
    beszel._efs_diag_last.clear()


def test_the_line_names_the_host_it_is_about():
    """A diagnostic that cannot be traced to a host is not a diagnostic."""
    _reset()
    out = _probe("nas01")
    assert "efs-aggregate nas01:" in out, out
    assert "efs-aggregate ?:" not in out


def test_the_host_key_is_not_recoverable_from_info_alone():
    """Why the caller has to pass it: this is the exact shape that produced
    360 question marks in production."""
    _reset()
    out = _probe("")
    assert "efs-aggregate ?:" in out, (
        "if this ever stops being '?', the fallback found a host key on `info` "
        "and the caller no longer needs to pass one")


def test_an_unchanged_aggregate_is_stated_once_not_every_tick():
    """The volume half. Ten probes of a host whose totals have not moved is
    one line, not ten."""
    _reset()
    first = _probe("nas01")
    assert "efs-aggregate nas01:" in first
    rest = "".join(_probe("nas01") for _ in range(9))
    assert rest == "", f"repeated unchanged probes still logged: {rest[:200]!r}"


def test_a_moved_aggregate_is_stated_again():
    """Quiet must not mean deaf — the number changing is the whole reason to
    print it."""
    _reset()
    _probe("nas01", used_gib=35.2)
    moved = _probe("nas01", used_gib=41.9)
    assert "efs-aggregate nas01:" in moved, "a changed total went unreported"
    assert "41.9" in moved


def test_hosts_do_not_silence_each_other():
    """The dedup is per host; a second host's first sighting still prints."""
    _reset()
    assert "nas01" in _probe("nas01")
    assert "nas02" in _probe("nas02")


def test_the_dedup_map_cannot_grow_without_bound():
    """Same bounded shape as `_warned_no_mounts` — a large fleet must not turn
    a log fix into a memory leak."""
    _reset()
    for i in range(beszel._EFS_DIAG_CAP + 50):
        _probe(f"host{i}")
    assert len(beszel._efs_diag_last) <= beszel._EFS_DIAG_CAP
