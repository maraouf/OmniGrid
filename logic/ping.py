"""TCP-connect (and optional ICMP) reachability probe — Ping host-stats provider.

Fifth host-stats provider. Unlike Beszel / Pulse / node-exporter
/ Webmin, Ping carries NO host telemetry beyond reachability + latency:
``host_ping_alive`` (bool), ``host_ping_rtt_ms`` (float),
``host_ping_loss_pct`` (float). It runs LAST in the merge order so its
coarse output never overwrites a richer provider's value — but on hosts
where every other provider is silent (a 5G modem, a VDSL router, a
power strip with a web UI), Ping is the ONE signal that proves the box
is up and on the network.

The probe is per-host opt-in via ``hosts_config[].ping.enabled`` (default
OFF). Most operators don't want OmniGrid TCP-syncing every router every
60s by default; they want to flip it on case-by-case for the boxes
where reachability matters.

Implementation strategy — TCP-connect first, ICMP second:

  - **TCP** (default): ``asyncio.open_connection(host, port)`` with
    ``asyncio.wait_for(timeout)``, measure round-trip, close, repeat
    ``count`` times. Works without CAP_NET_RAW (OmniGrid runs as a
    plain user inside the container), behaves identically across
    Docker / Swarm / k8s, no platform-specific quirks. Configurable
    port: per-host ``ping.port`` overrides ``ping_default_port``
    (default 443). Common alternates: 22, 80, 53, 7 (echo).
  - **ICMP** (opt-in): ``ping_use_icmp`` global flag plus ``icmplib``
    package present plus per-host ``ping.transport=='icmp'`` (or no
    override AND global flag set). Falls through to TCP on import
    error or insufficient privileges. Documented in the module
    docstring + the Settings panel hint.

Each successful TCP connect counts as ONE "received packet" for
loss-pct math; each timeout counts as one lost. DNS resolution failures
short-circuit to ``alive=False, error="DNS resolution failed"`` —
asyncio handles dual-stack (AF_INET6 / AF_INET) transparently; the
first family to succeed wins.

Cooldown: per-(host, port) `Cooldown` armed on TWO consecutive timeouts
so a permanently-unreachable host doesn't burn timeout budget every
tick. Same pattern as `logic/webmin.py` / `logic/ssh.py` (CONS-004 —
single shared `Cooldown` implementation in `logic/cooldown.py`).
"""
from __future__ import annotations

import asyncio
import socket
import time
from typing import Optional


# Per-(host, port) cool-down on consecutive timeouts. Same shared
# `tuning_auth_failure_cooldown_seconds` knob the Webmin / SSH consumers
# use — operator can tune all three at once. The Cooldown's seconds
# resolver re-reads on every arm() so a Save in Admin → Config takes
# effect on the next tick without restart.
from logic.cooldown import Cooldown as _Cooldown
from logic import tuning as _tuning
_unreachable_cooldown = _Cooldown(
    seconds_fn=lambda: _tuning.tuning_int("tuning_ping_cooldown_seconds")
)

# Tracks consecutive-failure count per (host, port) so the cooldown
# only arms on the SECOND timeout, not the first. A single transient
# blip shouldn't suppress probes for 5 minutes; two in a row is a
# stronger signal something is genuinely down.
_consecutive_failures: dict[tuple[str, int], int] = {}

# icmplib is OPTIONAL — the import goes lazy + safe so a deployment
# without the package still gets TCP probes. Operators on minimal
# images (alpine + python:3.12-slim default) won't have it; flagging
# it as optional keeps the boot path clean.
try:
    import icmplib  # type: ignore  # noqa: F401
    _HAS_ICMP = True
except ImportError:
    _HAS_ICMP = False


def has_icmp_support() -> bool:
    """Public probe — returns True iff ``icmplib`` is importable.

    Note this does NOT check whether the process has CAP_NET_RAW; that
    only manifests at probe time as ``icmplib.exceptions.SocketPermissionError``.
    The Settings tab uses this to decide whether to render the ICMP
    toggle as enabled or to surface a "package missing" hint.
    """
    return _HAS_ICMP


async def _probe_tcp_once(host: str, port: int, timeout_seconds: float) -> tuple[bool, Optional[float], Optional[str]]:
    """ONE TCP connect attempt. Returns ``(alive, rtt_ms, error)``.

    Success: ``(True, rtt_ms, None)``. Failure: ``(False, None, error)``.
    Errors are short canonical strings (``"timeout"``, ``"refused"``,
    ``"dns"``, ``"network"``) so the caller can pattern-match without
    parsing OS-specific messages. Timeout is the most common case and
    deserves its own bucket because the cooldown logic gates on it.
    """
    t0 = time.monotonic()
    try:
        # `wait_for` wraps `open_connection` to cap the connect itself,
        # not the whole DNS+SYN+SYNACK chain — DNS resolution is part
        # of `open_connection` so a slow resolver counts toward the
        # timeout budget. That's intentional: from the operator's POV,
        # "host is reachable" includes DNS, not just the TCP layer.
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        return False, None, "timeout"
    except ConnectionRefusedError:
        # Refused = host is up + reachable + port is closed. We treat
        # this as ALIVE (the box is on the network) but record the
        # RTT as the connect-fail latency. The drawer chart still
        # gets a signal; the operator can switch to a port that
        # accepts connections if they want a smoother latency curve.
        rtt_ms = (time.monotonic() - t0) * 1000.0
        return True, rtt_ms, None
    except (socket.gaierror, OSError) as e:
        # gaierror = DNS failure (no A/AAAA record, NXDOMAIN, no
        # resolver). OSError covers "no route to host", "network
        # unreachable", and the small zoo of platform-specific
        # connection errors. Both terminate the probe with no RTT.
        msg = str(e) or e.__class__.__name__
        # Heuristic: gaierror's class name is enough on its own; OSError's
        # message is operator-readable.
        if isinstance(e, socket.gaierror):
            return False, None, "dns"
        return False, None, f"network: {msg[:60]}"
    rtt_ms = (time.monotonic() - t0) * 1000.0
    # Best-effort close — we don't care if the peer hung up on us
    # mid-handshake, only that we got a connect.
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass
    return True, rtt_ms, None


async def _probe_icmp(host: str, count: int, timeout_seconds: float) -> dict:
    """ICMP echo via ``icmplib``. Returns the same dict shape as TCP.

    Optional path — only invoked when both ``ping_use_icmp`` AND
    ``icmplib`` are present AND the per-host transport agrees (or the
    operator-global fallback wins). On ImportError or
    SocketPermissionError, raises so the caller falls back to TCP.
    """
    if not _HAS_ICMP:
        raise RuntimeError("icmplib not installed")
    # icmplib's async API is synchronous-blocking under the hood — it
    # uses raw sockets which the event loop can't await. Wrap in a
    # thread-pool so we don't stall every other probe in the sampler
    # tick. Threadpool fan-out cost is trivial vs the wire latency.
    import functools
    loop = asyncio.get_running_loop()
    func = functools.partial(
        icmplib.ping, host,
        count=count, interval=0.2, timeout=timeout_seconds,
        privileged=False,  # unprivileged uses ICMP_FILTER socket type
    )
    h = await loop.run_in_executor(None, func)
    rtts = [r for r in (h.rtts or []) if r is not None]  # ms already
    sent = h.packets_sent or count
    rcv = h.packets_received or 0
    loss = (100.0 * (sent - rcv) / sent) if sent else 100.0
    avg = (sum(rtts) / len(rtts)) if rtts else None
    return {
        "alive": rcv > 0,
        "rtt_ms": avg,
        "rtt_min_ms": min(rtts) if rtts else None,
        "rtt_max_ms": max(rtts) if rtts else None,
        "loss_pct": float(loss),
        "packets_sent": int(sent),
        "packets_received": int(rcv),
        "error": None if rcv > 0 else "icmp: no echo reply",
    }


async def probe_ping(
    host: str,
    *,
    port: int = 443,
    transport: str = "tcp",
    timeout_seconds: float = 2.0,
    count: int = 3,
) -> dict:
    """Probe one host. See module docstring for the contract.

    ``transport``:
      - ``"tcp"`` (default) — TCP-connect probes on ``port``. Always
        works without elevated privileges.
      - ``"icmp"`` — raw ICMP echo via ``icmplib``. Falls back to TCP
        on ImportError / SocketPermissionError.

    Returns the ping-result dict; never raises (errors land in the
    ``error`` field). Cooldown is consulted up-front: if the
    (host, port) pair is in cooldown, returns immediately with
    ``alive=False, error="cooldown"`` and skips the wire entirely.
    """
    host_clean = (host or "").strip()
    if not host_clean:
        return {
            "alive": False, "rtt_ms": None,
            "rtt_min_ms": None, "rtt_max_ms": None,
            "loss_pct": 100.0,
            "packets_sent": 0, "packets_received": 0,
            "error": "no host",
        }
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        port_int = 443
    if not (1 <= port_int <= 65535):
        port_int = 443

    cool_key = (host_clean, port_int)
    if _unreachable_cooldown.remaining(*cool_key) is not None:
        return {
            "alive": False, "rtt_ms": None,
            "rtt_min_ms": None, "rtt_max_ms": None,
            "loss_pct": 100.0,
            "packets_sent": 0, "packets_received": 0,
            "error": "cooldown",
        }

    # ICMP path (with TCP fallback on import / capability error).
    if transport == "icmp" and _HAS_ICMP:
        try:
            return await _probe_icmp(host_clean, count, timeout_seconds)
        except Exception as e:
            print(f"[ping] {host_clean!r} ICMP failed ({e}); falling back to TCP")

    # TCP path — primary.
    rtts: list[float] = []
    sent = max(1, int(count or 1))
    rcv = 0
    last_err: Optional[str] = None
    for _ in range(sent):
        ok, rtt, err = await _probe_tcp_once(host_clean, port_int, timeout_seconds)
        if ok and rtt is not None:
            rcv += 1
            rtts.append(rtt)
        elif err:
            last_err = err
        # Inter-probe gap so consecutive connects don't merge into one
        # TCP retransmit on the kernel's side.
        await asyncio.sleep(0.05)
    loss = 100.0 * (sent - rcv) / sent
    avg = (sum(rtts) / len(rtts)) if rtts else None

    # Cooldown logic — only fires when EVERY probe in this batch
    # timed out. A single timeout in a batch of three doesn't trip it
    # (transient packet loss); a full-batch timeout twice in a row
    # does (the host is down). Successful probes clear the counter.
    if rcv == 0 and last_err == "timeout":
        n = _consecutive_failures.get(cool_key, 0) + 1
        _consecutive_failures[cool_key] = n
        if n >= 2:
            _unreachable_cooldown.arm(*cool_key)
            print(f"[ping] {host_clean}:{port_int} armed cooldown after "
                  f"{n} consecutive timeouts")
    else:
        if cool_key in _consecutive_failures:
            del _consecutive_failures[cool_key]
        _unreachable_cooldown.clear(*cool_key)

    return {
        "alive": rcv > 0,
        "rtt_ms": avg,
        "rtt_min_ms": min(rtts) if rtts else None,
        "rtt_max_ms": max(rtts) if rtts else None,
        "loss_pct": float(loss),
        "packets_sent": sent,
        "packets_received": rcv,
        "error": None if rcv > 0 else (last_err or "no response"),
    }


def to_host_stats(result: dict) -> dict:
    """Map a ``probe_ping`` result into the ``host_*`` schema.

    Used by the gather + per-host-merge paths. Empty-shape on a failed
    probe so ``_merge_best`` doesn't overwrite richer providers' fields
    with our reachability data.
    """
    out: dict = {}
    if result.get("alive"):
        out["host_ping_alive"] = True
        rtt = result.get("rtt_ms")
        if rtt is not None:
            out["host_ping_rtt_ms"] = float(rtt)
        loss = result.get("loss_pct")
        if loss is not None:
            out["host_ping_loss_pct"] = float(loss)
    else:
        # Even when down, surface the alive=False bool + loss=100 so
        # the SPA can render a "down" chip for opted-in hosts. RTT
        # stays absent because there's no measurement to report.
        out["host_ping_alive"] = False
        out["host_ping_loss_pct"] = float(result.get("loss_pct") or 100.0)
    return out
