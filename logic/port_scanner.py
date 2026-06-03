"""Port-scanner host-stats provider — Stage 1.

On-demand TCP-connect scan of a single host. Triggered from the host
drawer or the AI palette (admin-only); never from a scheduler in
Stage 1. Stage 2 will layer per-service follow-up actions on top of
the bare port-discovery surface.

Auth model reconnaissance (per the project's provider checklist):
  * No authentication — pure TCP-connect probes against the target's
    listening sockets. The probe IS the auth model; closed ports are
    indistinguishable from filtered ports under TCP-connect.
  * Unprivileged — no `cap_add: NET_RAW` plumbing required. SYN scan
    is deliberately deferred to a future iteration.
  * Side-effect surface — establishes a TCP handshake then closes
    immediately. Many services log "connection from X dropped before
    request" warnings; rate is bounded by `concurrency` so a scan of
    a healthy box doesn't drown its logs.

Public surface:

* ``async scan_host(target, ports, *, timeout_s, concurrency,
  banner_grab=False)`` — runs the scan, returns
  ``{host, scanned_at, ports: [{port, open: bool, banner_excerpt?}],
  duration_ms, error}``. Errors that prevent the whole scan
  (resolution failure, connect refused on every port) populate the
  top-level ``error`` field; per-port failures just leave that
  port's ``open=False``.

* ``DEFAULT_PORTS`` — top-100 well-known + common-app port list.
  Operator can override via the global setting or per-host.

* ``parse_port_csv(s)`` — accepts ``"22,80,443,8000-8100"`` style
  syntax, dedupes, sorts, clamps each port to 1..65535. Used by the
  endpoint to validate operator-supplied port lists before passing
  to ``scan_host``.

Service-hint mapping is a tiny lookup table (port → likely service
name) covered by ``hint_for_port``. NOT a fingerprint — it's just
"port 32400 is probably Plex" naming convenience for the SPA chip
labels. Banner-grab opt-in (Stage 2) will replace the hint with a
real fingerprint when the upstream supports it.
"""
from __future__ import annotations

import asyncio
import socket
import time
from typing import Awaitable, Iterable, Optional


async def gather_port_probes(
    coros: list[Awaitable[dict]],
    error_label: str,
) -> tuple[list[dict], Optional[str], int]:
    """Run a port-probe fan-out under a wall-clock timer.

    Shared by the TCP scanner here and the UDP scanner in
    ``logic.port_scanner_udp`` so the gather + timer + sort + error-
    classify scaffolding doesn't drift between the two. Returns
    ``(results, err, duration_ms)`` where ``results`` is sorted by
    ``port`` ascending. The error string is ``"<error_label>: <type>:
    <msg>"`` on failure, ``None`` on success.
    """
    t0 = time.monotonic()
    try:
        results: list[dict] = list(await asyncio.gather(*coros))
        err: Optional[str] = None
    except Exception as e:  # noqa: BLE001
        results = []
        err = f"{error_label}: {type(e).__name__}: {e}"
    duration_ms = int((time.monotonic() - t0) * 1000.0)
    results.sort(key=lambda r: r.get("port", 0))
    return results, err, duration_ms


# Top-100 ports — well-known service ports + commonly-used app ports
# (Plex, Sonarr, Radarr, qBittorrent, Portainer, Grafana, Prometheus,
# etc.) so a fresh scan of a homelab box surfaces useful chips out of
# the box. Operator can override via the global setting or per-host.
DEFAULT_PORTS: tuple[int, ...] = (
    20, 21, 22, 23, 25, 53, 67, 68, 69, 80, 81, 110, 111, 123, 135, 137, 138, 139,
    143, 161, 162, 179, 199, 389, 443, 445, 465, 514, 554, 587, 631, 636, 853, 873,
    989, 990, 993, 995, 1025, 1049, 1080, 1194, 1433, 1434, 1521, 1701, 1723,
    1883, 1900, 1935, 2049, 2082, 2083, 2086, 2087, 2095, 2096, 2375, 2376, 3000, 3001,
    3003, 3050, 3060, 3128, 3306, 3389, 3478, 3690, 4000, 4242, 4440, 4443, 4500,
    4711, 4747, 4848, 5000, 5001, 5002, 5050, 5051, 5055, 5060, 5061, 5216,
    5222, 5269, 5353, 5357, 5432, 5601, 5666, 5672, 5800, 5900, 5984, 5985,
    6160, 6162, 6379, 6443, 6660, 6667, 6767, 6881, 6969, 7000, 7001, 7474,
    7575, 7655, 7680, 7878, 7880, 7882, 7886, 7888, 8000, 8005, 8006, 8008, 8010, 8020,
    8027, 8080, 8081, 8085, 8086, 8088, 8089, 8090, 8091, 8095, 8096, 8123,
    8125, 8181, 8191, 8200, 8265, 8266, 8332, 8333, 8384, 8388, 8443, 8500,
    8530, 8554, 8581, 8631, 8746, 8765, 8888, 8920, 8989, 9000, 9001, 9080, 9090,
    9091, 9100, 9117, 9120, 9191, 9200, 9300, 9392, 9393, 9401, 9418, 9419,
    9443, 9500, 9696, 9981, 10000, 10001, 10050, 11211, 11443, 19999, 25565, 27017,
    21114, 21115, 21116, 21117, 21118, 21119,
    27018, 32400, 32469, 32500, 32769, 41080, 44444, 45876, 49669, 50000,
    51510, 51511, 51820, 53000, 57221,
    # Operator-requested additions:
    1514, 1515, 1516, 1517, 1518, 1519, 1520,  # syslog-TLS / Splunk fwd range (1521 oracle already above)
    5201,  # iPerf3
    5480,  # VMware vCenter Server Management (VAMI)
    9997,  # Splunk forwarder receiver (splunkd)
    10051,  # Zabbix server (active-agent + proxy connections)
    # Common well-known service ports — audit additions so a fresh
    # scan covers the standard infra/homelab port set out of the box.
    88, 113, 119, 264, 427, 444, 500, 502, 515, 543, 544, 548,
    593, 623, 660, 902, 1099, 1311, 1812, 1813, 2000, 2181, 2222,
    2377, 2379, 2380, 2483, 2484, 2525, 3260, 3268, 3269, 3300,
    4040, 4222, 4789, 4949, 5044, 5500, 5938, 5986, 6000, 7070,
    7946, 7990, 8042, 8083, 8087, 8118, 8140, 8161, 8444, 8883,
    8983, 9042, 9092, 9093, 9160, 9389, 9999, 10250, 10255, 10443,
    15672, 27015,
)

# Tiny lookup table — port → likely service name. This is NAMING
# convenience for chip labels, NOT a fingerprint. A connection to
# port 22 might be SSH, might be a custom app squatting on a
# well-known port; we just emit the most likely name and let the
# operator override via `hosts_config[].services[]`.
_PORT_HINTS: dict[int, str] = {
    20: "ftp-data", 21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp",
    53: "dns", 67: "dhcp", 68: "dhcp", 69: "tftp", 80: "http", 81: "npm-admin",
    110: "pop3", 111: "rpcbind", 123: "ntp", 135: "msrpc",
    137: "netbios-ns", 138: "netbios-dgm", 139: "smb",
    143: "imap", 161: "snmp", 162: "snmp-trap", 179: "bgp",
    199: "smux", 389: "ldap",
    443: "https", 445: "smb", 465: "smtps", 514: "syslog", 554: "rtsp",
    587: "submission",
    631: "ipp", 636: "ldaps", 853: "dns-over-tls", 873: "rsync", 989: "ftps-data", 990: "ftps",
    993: "imaps", 995: "pop3s", 1025: "msrpc", 1080: "socks", 1194: "openvpn",
    1049: "upnp", 1433: "mssql", 1434: "mssql", 1521: "oracle", 1701: "l2tp",
    1723: "pptp",
    1883: "mqtt", 1900: "ssdp", 1935: "rtmp", 2049: "nfs", 2082: "cpanel", 2083: "cpanel-ssl",
    2086: "whm", 2087: "whm-ssl", 2095: "webmail", 2096: "webmail-ssl",
    2375: "docker", 2376: "docker-ssl", 3000: "grafana",
    3001: "uptime-kuma", 3003: "tracearr",
    3050: "netbootxyz-web", 3060: "netbootxyz-assets",
    3128: "squid",
    3306: "mysql", 3389: "rdp", 3478: "stun", 3690: "svn", 4000: "icq",
    4242: "graphite", 4440: "rundeck", 4443: "https-alt", 4500: "ipsec", 4711: "mcsmadm",
    4747: "buschtrommel", 4848: "glassfish", 5000: "upnp", 5001: "synology",
    5002: "kavita",
    5050: "speedtest-tracker", 5051: "speedtest-tracker", 5055: "seerr",
    5060: "sip", 5061: "sips", 5216: "myspeed",
    5222: "xmpp-c2s", 5269: "xmpp-s2s",
    5353: "mdns", 5357: "wsdapi", 5432: "postgres", 5601: "kibana",
    5666: "nrpe", 5672: "amqp", 5800: "vnc-http", 5900: "vnc",
    5984: "couchdb", 5985: "winrm",
    6160: "veeam-mgmt", 6162: "veeam-transport", 6379: "redis",
    6443: "kubernetes", 6660: "irc", 6667: "irc",
    6767: "bazarr", 6881: "bittorrent", 6969: "bittorrent-tracker",
    7000: "afs", 7001: "weblogic", 7474: "neo4j",
    7575: "homarr", 7655: "pulse", 7680: "wudo",
    7878: "radarr", 7880: "sonarr", 7882: "lidarr", 7886: "prowlarr",
    7888: "readarr",
    8000: "http-alt", 8005: "apprise", 8006: "proxmox", 8008: "http-alt",
    8010: "ddns-updater", 8020: "manageengine", 8027: "manageengine",
    8080: "http-alt", 8081: "http-alt",
    8085: "dockge", 8086: "influxdb", 8088: "http-alt", 8089: "splunk",
    8090: "qbittorrent", 8091: "adguardhome-sync", 8095: "lubelogger",
    8096: "jellyfin", 8123: "homeassistant", 8125: "netdata-statsd",
    8181: "http-alt", 8191: "flaresolverr", 8200: "vault",
    8265: "tdarr-web", 8266: "tdarr-server",
    8332: "bitcoin", 8333: "bitcoin", 8384: "syncthing",
    8388: "shadowsocks", 8443: "https-alt", 8500: "consul", 8530: "wsus",
    8554: "diun", 8581: "homebridge",
    8631: "ipp-alt", 8746: "manageengine", 8765: "http-alt", 8888: "http-alt",
    8920: "jellyfin-https",
    8989: "sonarr",
    9000: "portainer", 9001: "portainer-edge", 9080: "http-alt",
    9090: "prometheus", 9091: "qbittorrent", 9100: "node-exporter",
    9117: "jackett", 9120: "komodo", 9191: "pulse-agent",
    9200: "elasticsearch", 9300: "elasticsearch",
    9392: "veeam-backup", 9393: "veeam-catalog", 9401: "veeam-secure",
    9418: "git", 9419: "veeam-rest", 9443: "portainer-https",
    9500: "omnigrid", 9696: "prowlarr", 9981: "tvheadend",
    10000: "webmin", 10001: "ubiquiti", 10050: "zabbix-agent",
    11211: "memcached", 11443: "unifi", 19999: "netdata", 25565: "minecraft",
    21114: "rustdesk", 21115: "rustdesk", 21116: "rustdesk",
    21117: "rustdesk", 21118: "rustdesk", 21119: "rustdesk",
    27017: "mongodb", 27018: "mongodb", 32400: "plex", 32469: "plex-dlna",
    32500: "plex", 32769: "aiohttp",
    41080: "deluge", 44444: "fing-agent", 45876: "beszel-agent",
    49669: "manageengine", 50000: "jenkins",
    51510: "qbittorrent", 51511: "qbittorrent", 51820: "wireguard",
    53000: "tautulli", 57221: "tailscale",
    1514: "syslog-tls", 1515: "syslog-tls", 1516: "syslog-tls",
    1517: "syslog-tls", 1518: "syslog-tls", 1519: "syslog-tls",
    1520: "syslog-tls",
    5201: "iperf3", 5480: "vcenter",
    9997: "splunk-fwd", 10051: "zabbix-server",
    # Common well-known service ports — audit additions.
    88: "kerberos", 113: "ident", 119: "nntp", 264: "bgmp",
    427: "svrloc", 444: "snpp", 500: "isakmp", 502: "modbus",
    515: "printer-lpd", 543: "klogin", 544: "kshell", 548: "afp",
    593: "http-rpc-epmap", 623: "ipmi", 660: "mac-srvr-admin",
    902: "vmware-auth", 1099: "java-rmi", 1311: "dell-omsa",
    1812: "radius", 1813: "radius-acct", 2000: "cisco-sccp",
    2181: "zookeeper", 2222: "ssh-alt", 2377: "docker-swarm",
    2379: "etcd-client", 2380: "etcd-peer", 2483: "oracle-db",
    2484: "oracle-ssl", 2525: "smtp-alt", 3260: "iscsi",
    3268: "ldap-gc", 3269: "ldap-gc-ssl", 3300: "ceph-mon",
    4040: "spark-ui", 4222: "nats", 4789: "vxlan", 4949: "munin",
    5044: "logstash-beats", 5500: "vnc-listener", 5938: "teamviewer",
    5986: "winrm-https", 6000: "x11", 7070: "realserver",
    7946: "swarm-gossip", 7990: "bitbucket", 8042: "hadoop-yarn",
    8083: "http-alt", 8087: "riak", 8118: "privoxy", 8140: "puppet",
    8161: "activemq", 8444: "https-alt", 8883: "mqtt-tls",
    8983: "solr", 9042: "cassandra", 9092: "kafka",
    9093: "alertmanager", 9160: "cassandra-thrift", 9389: "adws",
    9999: "http-alt", 10250: "kubelet", 10255: "kubelet-ro",
    10443: "https-alt", 15672: "rabbitmq-mgmt", 27015: "source-engine",
}


def hint_for_port(port: int) -> str:
    """Return a likely service name for the given port, or empty
    string when the port isn't in the lookup table. Naming convenience
    for chip labels — the SPA falls back to "port <N>" when this
    returns empty.
    """
    return _PORT_HINTS.get(int(port or 0), "")


def parse_port_csv(s: str) -> list[int]:
    """Parse the operator-supplied port string into a clean ascending
    list of unique ports. Accepts comma-separated single ports
    AND ``low-high`` ranges:

        "22,80,443,8000-8010" → [22, 80, 443, 8000, 8001, ..., 8010]

    Each port clamps to 1..65535. Invalid tokens silently drop. Empty
    input → empty list (caller falls back to ``DEFAULT_PORTS``).
    Range upper bounds clamp to 11000 ports per range so a single
    range can cover the typical service / app-port territory (1-1024
    well-knowns + the 1024-10000 ephemeral / common-service band)
    while still bounding the worst case.
    """
    if not s:
        return []
    seen: set[int] = set()
    for tok in str(s).split(","):
        tok = tok.strip()
        if not tok:
            continue
        if "-" in tok:
            try:
                lo_s, hi_s = tok.split("-", 1)
                lo = max(1, min(65535, int(lo_s.strip())))
                hi = max(1, min(65535, int(hi_s.strip())))
            except (TypeError, ValueError):
                continue
            if hi < lo:
                lo, hi = hi, lo
            # Cap each range at 11000 ports — operator typing
            # `1-65535` shouldn't produce a multi-hour scan, but
            # `1-11000` covers well-knowns + common-app territory.
            hi = min(hi, lo + 11000 - 1)
            for p in range(lo, hi + 1):
                seen.add(p)
        else:
            try:
                p = int(tok)
            except (TypeError, ValueError):
                continue
            if 1 <= p <= 65535:
                seen.add(p)
    return sorted(seen)


async def _probe_one_port(host: str, port: int, timeout_s: float,
                          banner_grab: bool) -> dict:
    """Probe one port. Returns a dict shape suitable for inclusion in
    the scan result's ``ports`` array. ``open: True`` means the TCP
    handshake completed; ``open: False`` covers timeout / refused /
    network unreachable. The closed-reason is recorded on the result
    so :func:`scan_host` can summarise failure modes in its log line
    (a fleet-wide "0 open ports" outcome usually means DNS failure
    or network unreachability, not actually-closed ports — operators
    need to be able to tell the difference).
    """
    out: dict = {"port": int(port), "open": False}
    writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout_s,
        )
        out["open"] = True
        if banner_grab:
            # Read up to 256 bytes — enough for an SSH banner / HTTP
            # greeting / SMTP welcome line without holding the slot
            # for slow services. Timeout flows through TUNABLES
            # (`tuning_port_scan_banner_read_seconds`, default 2s)
            # so noisy networks can raise it without redeploy.
            # Lazy-import to keep this module's import graph clean
            # (port_scanner is imported by main.py before tuning).
            from logic import tuning as _tuning
            from logic.tuning import Tunable
            banner_timeout = float(_tuning.tuning_int(Tunable.PORT_SCAN_BANNER_READ_SECONDS))
            try:
                data = await asyncio.wait_for(reader.read(256), timeout=banner_timeout)
                if data:
                    text = data.decode(errors="replace").strip()
                    # Strip control chars; cap at 200 chars so the JSON
                    # payload stays compact.
                    text = "".join(c for c in text if c.isprintable() or c in " \t")
                    if text:
                        out["banner_excerpt"] = text[:200]
            except (asyncio.TimeoutError, OSError):
                pass
    except asyncio.TimeoutError:
        out["_closed_reason"] = "timeout"
    except ConnectionRefusedError:
        out["_closed_reason"] = "refused"
    except socket.gaierror as e:
        out["_closed_reason"] = f"dns: {e}"
    except OSError as e:
        # ENETUNREACH / EHOSTUNREACH / EADDRNOTAVAIL etc. surface here.
        out["_closed_reason"] = f"oserror: {type(e).__name__}: {e}"
    finally:
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass
    return out


async def scan_host(
    target: str,
    ports: Iterable[int],
    *,
    timeout_s: float = 2.0,
    concurrency: int = 32,
    banner_grab: bool = False,
    diagnostic_ports: Optional[set[int]] = None,
) -> dict:
    """Run an asyncio TCP-connect scan against ``target``.

    Returns ``{host, scanned_at, ports: [{port, open, banner_excerpt?}],
    duration_ms, error}``. The result includes EVERY scanned port
    (open + closed) so the caller can diff against the previous scan
    without an additional "what was scanned" payload.

    ``concurrency`` caps the in-flight probes via a Semaphore. For a
    well-loaded firewall a tighter cap (8-16) is friendlier; for a
    homelab box on a quiet LAN, 32-64 finishes faster.
    """
    if not target:
        print("[port_scanner] no target — bailing")
        return {
            "host": target or "",
            "scanned_at": int(time.time()),
            "ports": [],
            "duration_ms": 0,
            "error": "no target",
        }
    port_list = sorted({int(p) for p in ports if isinstance(p, int) or str(p).isdigit()})
    if not port_list:
        port_list = list(DEFAULT_PORTS)
    timeout_s = max(0.1, min(30.0, float(timeout_s or 2.0)))
    concurrency = max(1, min(256, int(concurrency or 32)))
    sem = asyncio.Semaphore(concurrency)

    # Resolve the target hostname to an IP address ONCE upfront.
    # `asyncio.open_connection(host, port)` does this internally per
    # connect, but the resolved IP isn't exposed back to the caller —
    # so toast / log surfaces could only echo the literal hostname.
    # On a fleet where the host_id is a friendly alias (e.g. `ftth`,
    # `nas`, `pve`) that resolves via the container's resolver chain
    # (mDNS, /etc/hosts, Docker DNS, search domain), the operator can
    # trace results back to a wire-level IP for forensics by reading
    # `resolved_ip` from the scan result. Falls back to None when
    # resolution fails OR target is already a literal IP (str(target)
    # round-trips via getaddrinfo cleanly in both cases — IPv4 + IPv6).
    resolved_ip: str | None = None
    try:
        loop = asyncio.get_event_loop()
        infos = await loop.getaddrinfo(
            str(target), None,
            type=socket.SOCK_STREAM,
        )
        if infos:
            # Pick the first address-family entry. IPv4 preferred when
            # both are returned (matches asyncio.open_connection's
            # default happy-eyeballs behaviour).
            for fam, _socktype, _proto, _canon, sockaddr in infos:
                if fam == socket.AF_INET:
                    resolved_ip = str(sockaddr[0])
                    break
            if resolved_ip is None:
                resolved_ip = str(infos[0][4][0])
    except (socket.gaierror, OSError, IndexError):
        resolved_ip = None

    async def _bounded(p: int) -> dict:
        async with sem:
            return await _probe_one_port(target, p, timeout_s, banner_grab)

    results, err, duration_ms = await gather_port_probes(
        [_bounded(p) for p in port_list], "scan failed",
    )
    # Categorise the closed-reason distribution so a "0 open ports"
    # outcome explains itself in the log. Most-common failure mode
    # is the operator's tip-off:
    #   - all `dns:` → target hostname doesn't resolve from inside
    #     the OmniGrid container (try setting an alias / FQDN).
    #   - all `oserror: OSError: [Errno 113] EHOSTUNREACH` → no
    #     network route from the container to the target.
    #   - all `refused` → target IS reachable but nothing listening
    #     on the scanned ports (likely real, but check the firewall).
    #   - mostly `timeout` → firewall silently dropping packets, OR
    #     timeout_s is too short for the WAN link.
    open_count = sum(1 for r in results if r.get("open"))
    reason_counts: dict[str, int] = {}
    for r in results:
        if r.get("open"):
            continue
        reason = r.get("_closed_reason") or "closed"
        # Collapse OSError noise to a coarse bucket so the log line
        # stays scannable when 100 ports all reported the same
        # `oserror: OSError: [Errno 113] EHOSTUNREACH`.
        if reason.startswith("oserror:"):
            reason = reason.split(":", 2)[1].strip()  # → "OSError" or similar
            reason = "oserror_" + reason
        elif reason.startswith("dns:"):
            reason = "dns"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    reason_summary = ", ".join(f"{k}={v}" for k, v in sorted(reason_counts.items()))
    print(
        f"[port_scanner] target={target!r} ports_scanned={len(results)} "
        f"open={open_count} duration_ms={duration_ms} "
        f"reasons={reason_summary or '-'}"
    )
    # Per-port diagnostic for "ports of interest" (the host's curated /
    # catalog ports — e.g. a pinned Beszel agent on 45876). When such a
    # port is scanned but NOT open, log WHY before the reason is stripped,
    # so "port X isn't detected" becomes answerable from Admin -> Logs:
    #   reason=refused → nothing listening on that port (check the service)
    #   reason=timeout → SYN dropped (host firewall / not reachable from
    #                    the OmniGrid container / timeout too short)
    #   reason=dns     → the target alias doesn't resolve from the container
    # A port absent from results entirely means it wasn't in the scan list.
    # Uses neutral wording (no "fail"/"error") so the persistent-log
    # severity classifier doesn't paint a benign closed-port red.
    if diagnostic_ports:
        _by_port = {r.get("port"): r for r in results}
        for _dp in sorted(diagnostic_ports):
            _r = _by_port.get(_dp)
            if _r is None:
                print(f"[port_scanner] diagnostic port {_dp} was not in the scan list "
                      f"(target={target!r}) — not scanned")
            elif not _r.get("open"):
                _reason = _r.get("_closed_reason") or "closed"
                print(f"[port_scanner] diagnostic port {_dp} scanned but not open "
                      f"(target={target!r}, state={_reason})")
    # Strip internal-only `_closed_reason` field from the public
    # result — it's diagnostic-only, not part of the API contract.
    for r in results:
        r.pop("_closed_reason", None)
    return {
        "host": target,
        # Wire-level IP the OS resolved the target hostname to. None
        # when getaddrinfo() failed AND the target wasn't a literal
        # IP. Surfaced in toast / history so the operator can trace
        # results back beyond the alias they typed (e.g. `opnsense`
        # → `192.X.X.X`, `ftth` → `192.X.X.X` via search-domain
        # resolution chain in the container's resolv.conf).
        "resolved_ip": resolved_ip,
        "scanned_at": int(time.time()),
        "ports": results,
        "duration_ms": duration_ms,
        "error": err,
    }


def open_ports_only(scan_result: dict) -> list[dict]:
    """Convenience filter: return only the open-port entries from a
    scan result, each enriched with the service hint. Used by the
    endpoint when persisting to ``host_port_scans`` (only open ports
    get rows; closed-port rows would balloon the table on a /16
    range scan).
    """
    out: list[dict] = []
    for p in (scan_result.get("ports") or []):
        if p.get("open"):
            port_num = int(p.get("port") or 0)
            out.append({
                "port": port_num,
                "service_hint": hint_for_port(port_num),
                "banner_excerpt": p.get("banner_excerpt") or "",
            })
    return out


def diff_against_curated(open_ports: list[dict],
                         curated_services: Optional[list[dict]]) -> dict:
    """Diff a scan's open-port list against the operator's curated
    ``hosts_config[].services[]`` list. Returns
    ``{both: [...], detected_only: [...], curated_only: [...]}`` —
    each entry from `open_ports` is classified by whether the same
    port number appears in the curated list. ``curated_only`` carries
    curated services that DIDN'T match any open port (the listening
    service may have died, OR the curated entry is for a port not in
    the scan's port range).
    """
    curated = curated_services if isinstance(curated_services, list) else []
    curated_ports = {int(s.get("port") or 0): s for s in curated if isinstance(s, dict)}
    both: list[dict] = []
    detected_only: list[dict] = []
    matched_curated_ports: set[int] = set()
    for p in open_ports:
        pnum = int(p.get("port") or 0)
        if pnum in curated_ports:
            matched_curated_ports.add(pnum)
            both.append({
                **p,
                "curated": curated_ports[pnum],
            })
        else:
            detected_only.append(p)
    curated_only = [
        s for pnum, s in curated_ports.items()
        if pnum not in matched_curated_ports
    ]
    return {
        "both": both,
        "detected_only": detected_only,
        "curated_only": curated_only,
    }
