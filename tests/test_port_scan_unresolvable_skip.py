"""A name that does not resolve is not scanned 283 times to prove it.

Found by reading the running deployment's own log: 7,809 ERROR lines in one
day — 10% of everything written — every one of them

    [asyncio] UNHANDLED EXCEPTION: Future exception was never retrieved
    socket.gaierror: [Errno -2] Name or service not known

arriving in bursts the width of the scan concurrency cap. The scanner resolved
the target ONCE up front (for `resolved_ip`), then threw that away and handed
the HOSTNAME to every per-port probe, so `asyncio.open_connection` repeated the
same failing lookup per port. Each probe's 2s timeout fires before the
container's resolver finishes exhausting its search domains, so the abandoned
getaddrinfo future completes later with a gaierror nobody retrieves.

The boot-time DNS check already reported these hosts as unresolvable ("60 of
174 curated host targets are unresolved by the container's resolver"), so the
work was known-futile before it started: ~17 seconds of wall clock and 283
guaranteed failures per host, per scheduled scan.
"""
from __future__ import annotations

import asyncio
import socket

import pytest

from logic import port_scanner


class _Loop:
    """Stands in for the event loop so getaddrinfo can be steered."""

    def __init__(self, exc=None, infos=None):
        self.exc = exc
        self.infos = infos or []
        self.calls = 0

    async def getaddrinfo(self, *_a, **_kw):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.infos


_IPV4 = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", 0))]


def _scan(monkeypatch, loop, ports=(22, 80, 443), probe=None):
    monkeypatch.setattr(port_scanner.asyncio, "get_event_loop", lambda: loop)
    if probe is not None:
        monkeypatch.setattr(port_scanner, "_probe_one_port", probe)
    return asyncio.run(port_scanner.scan_host(
        "nosuchhost.invalid", list(ports), timeout_s=0.05, concurrency=4))


def test_an_unresolvable_target_is_not_probed_at_all(monkeypatch):
    """The load-bearing assertion: ZERO per-port probes. Each one it skips is
    one abandoned DNS future it does not leave behind."""
    probed = []

    async def _never(host, port, *_a, **_kw):
        probed.append(port)
        return {"port": port, "open": False}

    out = _scan(monkeypatch, _Loop(exc=socket.gaierror(-2, "Name or service not known")),
                ports=range(1, 284), probe=_never)
    assert probed == [], f"probed {len(probed)} port(s) against a name that does not resolve"
    assert out["ports"] == []
    assert out["resolved_ip"] is None


def test_the_skip_is_reported_not_silent(monkeypatch):
    """`resolved_ip: None` plus a dns error is what the caller already reads to
    say "this alias does not resolve" — returning early must not blank it."""
    out = _scan(monkeypatch, _Loop(exc=socket.gaierror(-2, "Name or service not known")))
    assert out["error"], "the skip reported no error at all"
    assert "dns" in out["error"].lower(), out["error"]
    assert out["host"] == "nosuchhost.invalid"
    assert out["duration_ms"] == 0


def test_no_synthesized_per_port_failures(monkeypatch):
    """`ports` stays EMPTY rather than 283 invented closed rows. Nothing was
    measured — a lookup that never reached a packet must not be reported as
    283 ports observed shut."""
    out = _scan(monkeypatch, _Loop(exc=socket.gaierror(-2, "no")), ports=range(1, 284))
    assert out["ports"] == [], "invented per-port results for a scan that never ran"


def test_the_result_still_carries_every_documented_key(monkeypatch):
    """Callers persist and diff this dict; an early return that drops a key
    would break them somewhere far from here."""
    out = _scan(monkeypatch, _Loop(exc=socket.gaierror(-2, "no")))
    for k in ("host", "resolved_ip", "scanned_at", "ports", "duration_ms", "error"):
        assert k in out, f"missing {k}"


def test_a_resolvable_target_is_still_scanned_normally(monkeypatch):
    """The other half — this must not become a scanner that never scans."""
    probed = []

    async def _probe(host, port, *_a, **_kw):
        probed.append(port)
        return {"port": port, "open": port == 22}

    out = _scan(monkeypatch, _Loop(infos=_IPV4), ports=(22, 80, 443), probe=_probe)
    assert sorted(probed) == [22, 80, 443], probed
    assert out["resolved_ip"] == "192.0.2.10"
    assert len(out["ports"]) == 3
    assert any(p.get("open") for p in out["ports"])


def test_resolution_is_attempted_exactly_once(monkeypatch):
    """The point of resolving up front is that it replaces N lookups with one.
    If this ever reads >1 the per-port re-resolution has crept back."""
    loop = _Loop(infos=_IPV4)

    async def _probe(host, port, *_a, **_kw):
        return {"port": port, "open": False}

    _scan(monkeypatch, loop, ports=range(1, 51), probe=_probe)
    assert loop.calls == 1, f"resolved {loop.calls} times for one scan"


@pytest.mark.parametrize("exc", [
    socket.gaierror(-2, "Name or service not known"),
    socket.gaierror(-5, "No address associated with hostname"),
    OSError("resolver unavailable"),
])
def test_every_resolution_failure_shape_skips(monkeypatch, exc):
    """Both gaierror codes seen in the live log, plus the generic OSError the
    resolver can raise when it is the resolver itself that is down."""
    out = _scan(monkeypatch, _Loop(exc=exc))
    assert out["ports"] == []
    assert out["resolved_ip"] is None
