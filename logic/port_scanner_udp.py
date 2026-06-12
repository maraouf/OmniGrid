"""UDP port scanner — Stage 2 companion to ``port_scanner.py``.

UDP is connectionless, so the TCP-scanner's "did the handshake
complete?" model doesn't apply. Instead, each well-known UDP
service has its own protocol-specific PROBE template — a small
formatted packet that elicits a response from a healthy server.

The scanner's classification is three-state:

* ``open`` — server replied with a parseable response. Strongest
  positive signal we have.
* ``open_filtered`` — silence within the timeout window. Could be
  open-but-quiet (server didn't respond to our particular probe
  shape; common for protocols that need authenticated handshakes)
  OR a firewall silently dropped the packet. UDP can't tell these
  apart without ICMP feedback.
* ``closed`` — ICMP "port unreachable" came back (host received
  the packet and explicitly told us nothing's listening). Rare on
  most modern firewalls — they suppress ICMP for stealth.

Protocols with built-in probe templates (Stage 2 ships these):

* **DNS** (53) — A query for ``.``
* **DHCP** (67) — DHCPDISCOVER (works against the unicast
  helper port, not the broadcast scenario)
* **TFTP** (69) — RRQ for a nonexistent file (server replies with
  ERROR packet)
* **NTP** (123) — NTPv4 client query (48 bytes, leader 0x1B)
* **NetBIOS-NS** (137) — node status request
* **SNMP** (161) — v2c GetRequest for ``sysDescr.0`` with the
  operator-configured community
* **IPMI-RMCP** (623) — Get Channel Authentication Capabilities
* **SSDP** (1900) — M-SEARCH discovery datagram
* **mDNS** (5353) — same shape as DNS

Out of scope for Stage 2 (need crypto handshakes, separate work):

* **Syslog** (514) — write-only protocol, no response
* **OpenVPN** (1194) — HMAC challenge / response
* **WireGuard** (51820) — Curve25519 + ChaCha20-Poly1305 handshake
* **L2TP** (1701) / **IPsec IKE** (500) — multi-step auth
* **RADIUS** (1812) — shared-secret HMAC

For ports without a known probe, we fall through to a generic
"send empty datagram, see if anyone answers" probe — almost always
yields ``open_filtered`` so we mark them as such.
"""
from __future__ import annotations

import asyncio
import errno
import random
import socket
import struct
import time
from typing import Iterable, Optional

# Default UDP port list — most-common services; covers ~95% of
# what an operator would care about on a typical homelab fleet.
DEFAULT_UDP_PORTS: tuple[int, ...] = (
    53,  # DNS
    67,  # DHCP server
    68,  # DHCP client (dhclient — ephemeral but operator-requested)
    69,  # TFTP
    123,  # NTP
    137,  # NetBIOS Name Service
    161,  # SNMP
    500,  # IPsec IKE (no probe — open_filtered only)
    514,  # Syslog (no response — open_filtered only)
    520,  # RIP routing
    623,  # IPMI-RMCP
    1194,  # OpenVPN (no probe — open_filtered only)
    1701,  # L2TP
    1812,  # RADIUS auth
    1813,  # RADIUS accounting
    1900,  # SSDP / UPnP
    4500,  # IPsec NAT-T
    5060,  # SIP
    5353,  # mDNS
    51820,  # WireGuard
    # Common well-known UDP service ports — audit additions so a scan
    # covers the standard infra/homelab UDP set out of the box.
    88,  # Kerberos
    111,  # rpcbind / portmapper
    138,  # NetBIOS Datagram
    162,  # SNMP trap
    177,  # XDMCP
    389,  # LDAP / CLDAP
    443,  # QUIC / HTTP3
    547,  # DHCPv6 server
    636,  # LDAPS
    1645,  # RADIUS auth (legacy)
    1646,  # RADIUS accounting (legacy)
    1985,  # Cisco HSRP
    2049,  # NFS
    3478,  # STUN / TURN
    4789,  # VXLAN (Docker Swarm overlay)
    5355,  # LLMNR
    6081,  # Geneve
    33434,  # traceroute
    49152,  # WS-Discovery / dynamic
)

_UDP_SERVICE_HINTS: dict[int, str] = {
    53: "DNS",
    67: "DHCP",
    68: "DHCP-client",
    69: "TFTP",
    123: "NTP",
    137: "NetBIOS-NS",
    138: "NetBIOS-DGM",
    161: "SNMP",
    162: "SNMP-trap",
    500: "IPsec-IKE",
    514: "Syslog",
    520: "RIP",
    623: "IPMI-RMCP",
    1194: "OpenVPN",
    1701: "L2TP",
    1812: "RADIUS",
    1813: "RADIUS-acct",
    1900: "SSDP",
    4500: "IPsec-NAT-T",
    5060: "SIP",
    5353: "mDNS",
    51820: "WireGuard",
    # Common well-known UDP service ports — audit additions.
    88: "Kerberos",
    111: "rpcbind",
    177: "XDMCP",
    389: "LDAP-CLDAP",
    443: "QUIC",
    547: "DHCPv6",
    636: "LDAPS",
    1645: "RADIUS-legacy",
    1646: "RADIUS-acct-legacy",
    1985: "HSRP",
    2049: "NFS",
    3478: "STUN",
    4789: "VXLAN",
    5355: "LLMNR",
    6081: "Geneve",
    33434: "traceroute",
    49152: "WS-Discovery",
}


# ---------------------------------------------------------------------------
# Probe templates — one helper per protocol. Each returns the bytes to send.
# ---------------------------------------------------------------------------

def _ntp_probe() -> bytes:
    """NTPv4 client query. Leader byte 0x1B = LI(0) | VN(3) | Mode(3-client).
    Server replies with a 48-byte response stamped with the system's clock.
    """
    return b"\x1b" + b"\x00" * 47


def _dns_probe() -> bytes:
    """DNS standard query for ``.`` with type ANY. Most servers reply
    with at least the root NS list. Transaction ID is randomised so
    concurrent probes don't collide.
    """
    txid = random.randint(0, 0xFFFF)
    flags = 0x0100  # standard query, recursion desired
    qdcount = 1
    header = struct.pack("!HHHHHH", txid, flags, qdcount, 0, 0, 0)
    # Question: name=`.` (single null byte), type=ANY(255), class=IN(1)
    question = b"\x00" + struct.pack("!HH", 255, 1)
    return header + question


def _mdns_probe() -> bytes:
    """mDNS uses the same wire format as DNS. Query the ``_services._dns-sd._udp.local``
    enumeration so any device running an mDNS responder shows up.
    """
    txid = 0
    flags = 0x0000  # standard query, no recursion
    header = struct.pack("!HHHHHH", txid, flags, 1, 0, 0, 0)
    # Encoded name: _services._dns-sd._udp.local
    name = (
        b"\x09_services"
        b"\x07_dns-sd"
        b"\x04_udp"
        b"\x05local"
        b"\x00"
    )
    question = name + struct.pack("!HH", 12, 1)  # PTR(12), IN(1)
    return header + question


def _dhcp_probe() -> bytes:
    """DHCPDISCOVER targeting the host directly (unicast — works
    against a server that has the helper port open even without the
    broadcast dance). Returns a minimal valid BOOTP+DHCP packet.
    """
    op = 1  # BOOTREQUEST
    htype = 1  # Ethernet
    hlen = 6
    hops = 0
    xid = random.randint(0, 0xFFFFFFFF)
    secs = 0
    flags = 0x8000  # broadcast bit
    ciaddr = b"\x00" * 4
    yiaddr = b"\x00" * 4
    siaddr = b"\x00" * 4
    giaddr = b"\x00" * 4
    chaddr = bytes(random.randint(0, 255) for _ in range(6)) + b"\x00" * 10
    sname = b"\x00" * 64
    file_ = b"\x00" * 128
    magic = b"\x63\x82\x53\x63"  # DHCP magic cookie
    # Options: type=DISCOVER (53,1,1), end (255)
    options = b"\x35\x01\x01" + b"\xff"
    # Pad to BOOTP minimum payload
    options += b"\x00" * (60 - len(options))
    return (
        struct.pack("!BBBBIHH", op, htype, hlen, hops, xid, secs, flags)
        + ciaddr + yiaddr + siaddr + giaddr + chaddr + sname + file_
        + magic + options
    )


def _snmp_probe(community: str = "public") -> bytes:
    """SNMP v2c GetRequest for ``sysDescr.0`` (1.3.6.1.2.1.1.1.0).
    Hand-rolled BER encoding to keep this module dep-free
    (pysnmp would make this 10 lines, but it's a heavy import).
    """
    # OID 1.3.6.1.2.1.1.1.0 BER-encoded
    oid_bytes = b"\x2b\x06\x01\x02\x01\x01\x01\x00"  # 1.3 packed → 0x2b, then each
    # VarBind: SEQUENCE { OID(0x06), NULL(0x05) }
    varbind = bytes([0x06, len(oid_bytes)]) + oid_bytes + b"\x05\x00"
    varbind = bytes([0x30, len(varbind)]) + varbind
    # VarBindList: SEQUENCE OF VarBind
    varbind_list = bytes([0x30, len(varbind)]) + varbind
    # PDU: GetRequest [0xA0] { request-id INTEGER, error-status INTEGER, error-index INTEGER, varbind-list }
    request_id = random.randint(1, 0x7FFFFFFF)
    rid_bytes = request_id.to_bytes(4, "big")  # explicit big-endian (matches the file's convention)
    pdu_body = (
        b"\x02\x04" + rid_bytes
        + b"\x02\x01\x00"  # error-status = 0
        + b"\x02\x01\x00"  # error-index = 0
        + varbind_list
    )
    pdu = bytes([0xA0, len(pdu_body)]) + pdu_body
    # SNMP message: SEQUENCE { version INTEGER, community OCTET STRING, pdu }
    community_bytes = community.encode("ascii", errors="ignore") or b"public"
    msg_body = (
        b"\x02\x01\x01"  # version = 1 (v2c)
        + bytes([0x04, len(community_bytes)]) + community_bytes
        + pdu
    )
    return bytes([0x30, len(msg_body)]) + msg_body


def _netbios_probe() -> bytes:
    """NetBIOS Name Service node-status request. Asks for a name list
    on the wildcard `*` query — Windows boxes reply with their
    machine name + workgroup.
    """
    txid = random.randint(0, 0xFFFF)
    flags = 0x0010  # standard query, broadcast
    header = struct.pack("!HHHHHH", txid, flags, 1, 0, 0, 0)
    # Encoded `*` name (32 chars, second-level DNS encoding of the
    # 16-byte NetBIOS name padded to 32 bytes)
    encoded = b"\x20" + b"CKAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" + b"\x00"
    question = encoded + struct.pack("!HH", 0x21, 1)  # NBSTAT(0x21), IN(1)
    return header + question


def _tftp_probe() -> bytes:
    """TFTP RRQ for a clearly-nonexistent filename. Server replies with
    ERROR packet (opcode 5) "File not found" — that's a valid open
    signal even though the read itself fails.
    """
    # Opcode 1 (RRQ) | filename "omnigrid_probe.nope" | 0 | "octet" | 0
    return b"\x00\x01" + b"omnigrid_probe.nope\x00octet\x00"


def _ipmi_probe() -> bytes:
    """IPMI-RMCP Get Channel Authentication Capabilities. Tests the IPMI
    daemon's auth presentation layer without sending any credentials.
    """
    # RMCP header: version(0x06), reserved(0x00), seq(0xFF), class(0x07=IPMI)
    rmcp = b"\x06\x00\xff\x07"
    # IPMI session header (no auth, sequence 0)
    ipmi_session = b"\x00\x00\x00\x00\x00\x00\x00\x00\x00"  # auth_type, sess_seq, sess_id, msg_len placeholder
    # IPMI message: rsAddr | netFn/lun | checksum | rqAddr | rqSeq/lun | cmd | data | checksum
    rs_addr = 0x20
    net_fn = 0x06 << 2  # NetFn=App, LUN=0
    cs1 = (0x100 - ((rs_addr + net_fn) & 0xFF)) & 0xFF
    rq_addr = 0x81
    rq_seq = 0x00
    cmd = 0x38  # Get Channel Auth Cap
    data = b"\x0E\x04"  # channel=0x0E (current), priv=0x04 (admin)
    msg_no_cs = bytes([rs_addr, net_fn, cs1, rq_addr, rq_seq, cmd]) + data
    cs2 = (0x100 - sum(msg_no_cs[3:]) & 0xFF) & 0xFF
    ipmi_msg = msg_no_cs + bytes([cs2])
    # Now patch the session msg_len byte (last byte of session header)
    session = ipmi_session[:-1] + bytes([len(ipmi_msg)])
    return rmcp + session + ipmi_msg


def _ssdp_probe() -> bytes:
    """SSDP M-SEARCH for everything (`ssdp:all`). Devices reply with an
    HTTP/1.1 200 OK over UDP listing their device descriptor URL.
    """
    return (
        b"M-SEARCH * HTTP/1.1\r\n"
        b"HOST: 239.255.255.250:1900\r\n"
        b"MAN: \"ssdp:discover\"\r\n"
        b"MX: 1\r\n"
        b"ST: ssdp:all\r\n"
        b"\r\n"
    )


def _generic_probe() -> bytes:
    """Fallback for ports without a known protocol — single null byte.
    Most services won't reply; result will be `open_filtered` unless
    the host explicitly returns ICMP unreachable.
    """
    return b"\x00"


# Probe routing — port → builder. Some builders take args (community);
# the caller supplies kwargs in `_probe_for(port, **kwargs)`.
def _probe_for(port: int, *, snmp_community: str = "public") -> Optional[bytes]:
    if port == 53:    return _dns_probe()
    if port == 67:    return _dhcp_probe()
    if port == 69:    return _tftp_probe()
    if port == 123:   return _ntp_probe()
    if port == 137:   return _netbios_probe()
    if port == 161:   return _snmp_probe(snmp_community)
    if port == 162:   return _snmp_probe(snmp_community)  # SNMP trap port — same shape
    if port == 623:   return _ipmi_probe()
    if port == 1900:  return _ssdp_probe()
    if port == 5353:  return _mdns_probe()
    return _generic_probe()


# ---------------------------------------------------------------------------
# Async UDP probe — sends a datagram, awaits a single response.
# ---------------------------------------------------------------------------

class _UdpProtocol(asyncio.DatagramProtocol):
    """Single-shot DatagramProtocol — completes a Future when the
    first response (or ICMP error) lands.
    """

    def __init__(self, future: asyncio.Future):
        self._future = future
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport):
        """asyncio DatagramProtocol hook: store the transport for write access."""
        self.transport = transport

    # noinspection PyUnusedLocal
    def datagram_received(self, data: bytes, addr) -> None:
        """asyncio DatagramProtocol hook: resolve the probe future on any reply."""
        if not self._future.done():
            self._future.set_result(("open", data))

    def error_received(self, exc):
        """asyncio DatagramProtocol hook: peer/router sent an ICMP error.

        ONLY ICMP port-unreachable (ECONNREFUSED) means the host received the
        probe and explicitly has nothing listening → ``closed``. Host-/net-
        unreachable + admin-prohibited (EHOSTUNREACH / ENETUNREACH / EACCES,
        ICMP type-3 codes 9/10/13 from a firewall or intermediate router) are
        NOT a closed port — they're ``open_filtered`` (a filtered/blocked
        path), not a definitive negative."""
        if self._future.done():
            return
        eno = getattr(exc, "errno", None)
        if eno == errno.ECONNREFUSED:
            self._future.set_result(("closed", str(exc)))
        else:
            # EHOSTUNREACH / ENETUNREACH / EACCES / anything else → filtered.
            self._future.set_result(("open_filtered", str(exc)))

    def connection_lost(self, exc):
        """asyncio DatagramProtocol hook: transport gone early → mark open|filtered."""
        # Transport closed before we got a response. Not normally
        # how UDP fails, but propagate as filtered.
        if not self._future.done() and exc is not None:
            self._future.set_result(("open_filtered", str(exc)))


async def _probe_one_udp(host: str, port: int, timeout_s: float, *,
                         snmp_community: str = "public") -> dict:
    """Probe one UDP port. Returns a dict shape suitable for inclusion
    in the scan result's ``ports`` array.

    State values:
    * ``open`` — got a response packet
    * ``closed`` — got ICMP unreachable (rare; most firewalls drop)
    * ``open_filtered`` — silence within the timeout
    """
    out: dict = {"port": int(port), "protocol": "udp", "open": False, "state": "open_filtered"}
    probe = _probe_for(port, snmp_community=snmp_community)
    if probe is None:
        return out
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    transport = None
    try:
        # `family=AF_UNSPEC`-style behaviour: let getaddrinfo pick.
        transport, _proto = await loop.create_datagram_endpoint(
            lambda: _UdpProtocol(fut),
            remote_addr=(host, port),
        )
        transport.sendto(probe)
        try:
            state, payload = await asyncio.wait_for(fut, timeout=timeout_s)
        except asyncio.TimeoutError:
            state, payload = "open_filtered", None
        if state == "open":
            out["open"] = True
            out["state"] = "open"
            # Capture a banner excerpt — first 200 printable chars of
            # the response. Most UDP services return binary; we
            # render it as best-effort UTF-8 with replacement chars.
            try:
                txt = (payload or b"").decode(errors="replace").strip()
                txt = "".join(c for c in txt if c.isprintable() or c in " \t")
                if txt:
                    out["banner_excerpt"] = txt[:200]
            except (UnicodeDecodeError, ValueError):
                pass
        elif state == "closed":
            out["state"] = "closed"
        else:
            out["state"] = "open_filtered"
    except (socket.gaierror, OSError) as e:
        out["_closed_reason"] = f"{type(e).__name__}: {e}"
    finally:
        if transport is not None:
            try:
                transport.close()
            except OSError:
                pass
    return out


# ---------------------------------------------------------------------------
# Public entry point — same shape as TCP scanner.
# ---------------------------------------------------------------------------

async def udp_scan_host(
    target: str,
    ports: Iterable[int],
    *,
    timeout_s: float = 3.0,
    concurrency: int = 8,
    snmp_community: str = "public",
) -> dict:
    """Run a UDP scan against ``target``. Returns a dict matching the
    TCP scanner's shape PLUS a `protocol: 'udp'` annotation per port:

    ``{host, scanned_at, ports: [{port, protocol, open, state, banner_excerpt?}],
    duration_ms, error}``.

    Default timeout (3s) is longer than TCP's (2s) — UDP probes have
    no handshake to short-circuit, so a slow service legitimately
    needs the full window. Concurrency cap (8) is friendlier than
    TCP's 32 because UDP traffic is more conspicuous on the network.
    """
    if not target:
        return {
            "host": target or "",
            "scanned_at": int(time.time()),
            "ports": [],
            "duration_ms": 0,
            "error": "no target",
        }
    port_list = sorted({int(p) for p in ports
                        if (isinstance(p, int) or str(p).isdigit())
                        and 0 < int(p) <= 65535})
    if not port_list:
        port_list = list(DEFAULT_UDP_PORTS)
    timeout_s = max(0.1, min(30.0, float(timeout_s or 3.0)))
    # Cap at the TCP ceiling (32), never above — UDP is intentionally LOWER
    # than TCP (more IDS-visible), so the clamp must not allow 2x the TCP cap.
    concurrency = max(1, min(32, int(concurrency or 8)))
    sem = asyncio.Semaphore(concurrency)

    async def _bounded(p: int) -> dict:
        async with sem:
            return await _probe_one_udp(target, p, timeout_s,
                                        snmp_community=snmp_community)

    from logic.port_scanner import gather_port_probes as _gather_port_probes
    results, err, duration_ms = await _gather_port_probes(
        [_bounded(p) for p in port_list], "udp scan failed",
    )
    open_count = sum(1 for r in results if r.get("open"))
    state_counts: dict[str, int] = {}
    for r in results:
        state_counts[r.get("state", "open_filtered")] = state_counts.get(r.get("state", "open_filtered"), 0) + 1
    state_summary = ", ".join(f"{k}={v}" for k, v in sorted(state_counts.items()))
    print(
        f"[port_scanner_udp] target={target!r} ports_scanned={len(results)} "
        f"open={open_count} duration_ms={duration_ms} "
        f"states={state_summary or '-'}"
    )
    # Strip internal-only diagnostic field from the public result.
    for r in results:
        r.pop("_closed_reason", None)
    return {
        "host": target,
        "scanned_at": int(time.time()),
        "ports": results,
        "duration_ms": duration_ms,
        "error": err,
    }


def open_udp_ports_only(scan_result: dict) -> list[dict]:
    """Convenience filter mirroring TCP's `open_ports_only`. Returns
    only ports with `state == 'open'` (NOT `open_filtered` — that's
    too noisy to persist). Each entry is enriched with the service
    hint.
    """
    out: list[dict] = []
    for p in (scan_result.get("ports") or []):
        if p.get("state") != "open":
            continue
        port = int(p.get("port") or 0)
        out.append({
            "port": port,
            "protocol": "udp",
            "service_hint": _UDP_SERVICE_HINTS.get(port, ""),
            "banner_excerpt": p.get("banner_excerpt") or "",
        })
    return out
