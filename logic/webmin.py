"""Webmin integration — read-only consumer of a Webmin Miniserv API.

Webmin (webmin.com) is a long-standing web-based admin UI for Unix-like
hosts. It ships its own web server (Miniserv.pl) and exposes every module
as a Perl CGI under ``/<module>/<script>.cgi``. Appending ``?xml=1``
(sometimes ``?json=1``) to most module paths toggles machine-readable
output — this is what we consume.

For OmniGrid's Hosts tab Webmin fills gaps the other three providers
don't cover — pending package updates, per-mount disk, network interface
detail. It runs LAST in the merge chain (most specific / highest
authority) but deliberately skips ``host_cpu_percent`` so Beszel /
node-exporter's smoother CPU reading wins.

Phase 1 covers four modules:
  - ``system-status`` — hostname, kernel, OS, cores, memory, uptime
  - ``package-updates`` — pending + security counts
  - ``mount`` — per-mount filesystem totals + used
  - ``net`` — interface list with addresses

Auth: HTTP Basic with a dedicated read-only Webmin user. This sidesteps
the session-cookie + CSRF dance; the operator enables Basic for the
API user via ``no_session=<user>=<name>`` in ``/etc/webmin/miniserv.conf``.
See ``notes/notes_agent_research.txt`` (Round 2026-04-24 evening —
Webmin) for the full rationale.

Units: Webmin is unit-inconsistent across modules. ``system-status``
returns memory in KiB; ``mount`` returns disk in bytes; ``uptime`` is
often a localised string. Every extractor normalises at its boundary
to the OmniGrid ``host_*`` schema (bytes everywhere, seconds for
uptime). Do not trust provider-native units downstream.

Lockout: Webmin locks accounts after N failed logins. On any 401 we
engage a 5-min cool-down keyed by ``(url, user)`` so a stale credential
doesn't hammer the target. Beats "re-try on every gather" semantics.

A future ``logic/cockpit.py`` could mirror this contract for RHEL-heavy
deployments where Webmin isn't the norm.
"""
from __future__ import annotations

import asyncio
import time
import re
from typing import Optional
from xml.etree import ElementTree as ET

import httpx


_AUTH_COOLDOWN_SECONDS = 300
_auth_cooldown: dict[tuple[str, str], float] = {}


def _cooldown_key(base_url: str, user: str) -> tuple[str, str]:
    return (base_url.rstrip("/"), user or "")


def _in_cooldown(base_url: str, user: str) -> Optional[float]:
    """Return remaining cool-down seconds (>0) if we're still backing
    off from a recent 401, or ``None`` when the probe can proceed."""
    key = _cooldown_key(base_url, user)
    expires = _auth_cooldown.get(key)
    if not expires:
        return None
    remaining = expires - time.time()
    if remaining <= 0:
        _auth_cooldown.pop(key, None)
        return None
    return remaining


def _arm_cooldown(base_url: str, user: str) -> None:
    _auth_cooldown[_cooldown_key(base_url, user)] = (
        time.time() + _AUTH_COOLDOWN_SECONDS
    )


async def _session_login(
    client: httpx.AsyncClient,
    base_url: str,
    user: str,
    password: str,
) -> bool:
    """POST Webmin's ``/session_login.cgi`` to establish a session cookie.

    Out of the box, Miniserv requires a cookie-based session and
    redirects Authorization-header callers to an HTML login page. This
    helper logs in via the session endpoint and — on success — leaves
    a ``sid=`` cookie on the httpx client that subsequent XML fetches
    will carry automatically. Avoids requiring operators to set
    ``no_session=1`` in ``/etc/webmin/miniserv.conf`` on every host.

    Returns True when a non-placeholder ``sid`` cookie is present
    after the POST. False on any failure; caller can fall back to
    Basic auth which works for installs with ``no_session=1``.

    Webmin's sid cookie defaults to ~1 hour of validity; we're using
    the client-scoped cookie jar so the session lives only for the
    duration of this probe (four parallel XML fetches). A future
    optimisation could cache the cookie across probes — skipping it
    here keeps the state model trivial.
    """
    url = base_url.rstrip("/") + "/session_login.cgi"
    try:
        r = await client.post(
            url,
            data={"user": user, "pass": password, "save": "1"},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                # Some Webmin versions enforce a Referer check on login
                # POSTs; setting it to the host's own URL satisfies the
                # check without leaking our own hostname.
                "Referer": base_url.rstrip("/") + "/",
            },
        )
    except Exception as e:
        print(f"[webmin] session_login failed: {e}")
        return False

    # Webmin sets cookies under various names across versions (``sid``,
    # ``sessid``, sometimes ``webmin``); accept any that isn't the "x"
    # logout placeholder. Case-insensitive because some proxies lowercase
    # cookie names en route.
    for name, value in (client.cookies or {}).items():
        lname = (name or "").lower()
        if lname in ("sid", "sessid", "webmin") and value and value.lower() not in ("", "x"):
            return True

    # Some builds redirect on success and set the cookie on the landing
    # response; httpx follow_redirects=True aggregates cookies, but the
    # server may also return 200 with a fresh login page on failure.
    # Detect the login form body to give the caller a clean False.
    body = (r.text or "").lstrip().lower()
    if body.startswith("<!doctype") or body.startswith("<html"):
        # Still on the login page — credentials rejected OR login form
        # wasn't found at this path (some reverse-proxied setups).
        return False
    return False


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _strip_html(body: str) -> str:
    """Extract a short human-readable line from a Webmin HTML error body.

    Webmin emits full HTML pages for 'Security warning' (referrer check),
    'Login required', etc. The ``<title>`` or first heading is usually
    enough to surface the actual failure reason.
    """
    if not body:
        return ""
    m = re.search(r"<title[^>]*>([^<]+)</title>", body, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    text = re.sub(r"<[^>]+>", " ", body)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:200]


async def _fetch_xml(
    client: httpx.AsyncClient,
    base_url: str,
    path: str,
    user: str,
) -> tuple[Optional[ET.Element], Optional[str]]:
    """GET ``base_url + path`` and parse the response as XML.

    Returns ``(root_element, None)`` on success or ``(None, error)``
    on any failure. Arms the auth cool-down on 401. Never raises.
    """
    url = base_url.rstrip("/") + path
    try:
        r = await client.get(url)
    except Exception as e:
        return None, f"{path}: {e}"
    if r.status_code == 401:
        _arm_cooldown(base_url, user)
        return None, f"{path}: HTTP 401 — cool-down armed"
    if r.status_code == 403:
        hint = _strip_html(r.text)
        return None, (f"{path}: HTTP 403"
                      + (f" — {hint}" if hint else ""))
    if r.status_code >= 400:
        return None, f"{path}: HTTP {r.status_code}"
    body = r.text or ""
    if not body.strip():
        return None, f"{path}: empty response"
    # Webmin sometimes returns a login HTML page for unauthenticated
    # probes when Basic isn't whitelisted for the user. Detect the
    # tell-tale ``<html`` prefix and surface a cleaner error.
    stripped = body.lstrip().lower()
    if stripped.startswith("<!doctype html") or stripped.startswith("<html"):
        hint = _strip_html(body)
        return None, (f"{path}: expected XML, got HTML"
                      + (f" — {hint}" if hint else ""))
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        return None, f"{path}: XML parse error — {e}"
    return root, None


def _findtext(root: ET.Element, *names: str) -> str:
    """Return the first non-empty text among attributes / child elements
    named in ``names`` (case-insensitive)."""
    for n in names:
        v = root.get(n)
        if v not in (None, ""):
            return str(v).strip()
        for child in root:
            if child.tag.lower() == n.lower():
                if child.text and child.text.strip():
                    return child.text.strip()
    return ""


def _parse_uptime_s(raw) -> int:
    """Coerce a Webmin uptime value to seconds.

    ``system-status`` emits uptime in three shapes depending on version
    and locale:
      - ``uptime_seconds`` / ``seconds`` — integer seconds (Webmin 2.1+)
      - ``seconds=...`` — attribute form sometimes nested
      - Localised string like ``"14 days, 7 hours, 22 min"`` — pre-2.1

    We accept any of them and return an int (0 on unparseable).
    """
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    s = str(raw).strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    total = 0
    patterns = [
        (r"(\d+)\s*d(?:ay)?s?\b",    86400),
        (r"(\d+)\s*h(?:our)?s?\b",   3600),
        (r"(\d+)\s*m(?:in)?(?:ute)?s?\b", 60),
        (r"(\d+)\s*s(?:ec)?(?:ond)?s?\b", 1),
    ]
    for pat, mult in patterns:
        for m in re.finditer(pat, s, re.IGNORECASE):
            total += int(m.group(1)) * mult
    return total


def extract_system_status(root: ET.Element) -> dict:
    """Shape the ``system-status?xml=1`` response into host_* fields.

    Webmin's XML element / attribute names have shifted between releases;
    we accept every variant we've seen documented. Memory is KiB on the
    wire — multiply by 1024 before emitting bytes.
    """
    if root is None:
        return {}
    # Webmin wraps the payload in a root element; the payload may be the
    # root itself or a child like ``<system>``. Walk both.
    scopes: list[ET.Element] = [root]
    for tag in ("system", "status", "system-status", "info", "host"):
        for child in root:
            if child.tag.lower() == tag:
                scopes.append(child)

    def pick(*names: str) -> str:
        for sc in scopes:
            v = _findtext(sc, *names)
            if v:
                return v
        return ""

    hostname = pick("hostname", "host", "name")
    kernel   = pick("kernel", "kernel_release", "release", "os_version")
    distro   = pick("distro", "os", "pretty_name", "os_name", "os_release")
    arch     = pick("arch", "architecture", "machine")
    cpu_type = pick("cpu_type", "cpu_model", "model", "cpu")
    cpus_raw = pick("cpus", "cores", "ncpus")
    cores    = int(_num(cpus_raw)) if cpus_raw else 0
    real_mem = _num(pick("real_mem", "mem_total", "memory_total"))
    free_mem = _num(pick("free_mem", "mem_free", "memory_free"))
    uptime_raw = (
        pick("uptime_seconds", "seconds")
        or pick("uptime")
    )
    uptime_s = _parse_uptime_s(uptime_raw)
    load_raw = pick("cpu_load", "load", "loadavg")
    load_parts = [p for p in re.split(r"[\s,]+", load_raw) if p]
    load_1m = _num(load_parts[0]) if len(load_parts) > 0 else 0.0
    load_5m = _num(load_parts[1]) if len(load_parts) > 1 else 0.0
    load_15 = _num(load_parts[2]) if len(load_parts) > 2 else 0.0

    mem_total_bytes = int(real_mem * 1024) if real_mem > 0 else 0
    mem_used_bytes = 0
    if real_mem > 0 and free_mem >= 0:
        mem_used_bytes = int((real_mem - free_mem) * 1024)
        if mem_used_bytes < 0:
            mem_used_bytes = 0

    host_boot_ts = (time.time() - uptime_s) if uptime_s > 0 else None
    return {
        "host_hostname":   hostname,
        "host_kernel":     kernel,
        "host_os":         distro,
        "host_platform":   distro.split()[0] if distro else "",
        "host_arch":       arch,
        "host_cpu_model":  cpu_type,
        "host_cores":      cores,
        "host_mem_total":  mem_total_bytes,
        "host_mem_used":   mem_used_bytes,
        "host_mem_avail":  max(0, mem_total_bytes - mem_used_bytes),
        "host_uptime_s":   uptime_s,
        "host_boot_ts":    host_boot_ts,
        "host_load_1m":    load_1m,
        "host_load_5m":    load_5m,
        "host_load_15m":   load_15,
    }


def extract_package_updates(root: ET.Element) -> dict:
    """Shape the ``package-updates`` response into update counters.

    Accepts both ``mode=count`` (returns just numbers) and the default
    listing (full ``<updates>`` array). Security count defaults to 0 if
    Webmin's schema doesn't flag severity on this distro.
    """
    if root is None:
        return {}
    pending = 0
    security = 0
    # Attribute-style: <updates total="19" security="12" />
    for name in ("updates", "update_count", "total", "pending"):
        v = root.get(name)
        if v not in (None, ""):
            try:
                pending = int(float(v))
                break
            except ValueError:
                continue
    for name in ("security", "security_count"):
        v = root.get(name)
        if v not in (None, ""):
            try:
                security = int(float(v))
                break
            except ValueError:
                continue
    # Element-style: walk children and count or tally.
    if pending == 0:
        count_from_list = 0
        security_from_list = 0
        saw_list = False
        for child in root.iter():
            tag = child.tag.lower()
            if tag in ("update", "package", "pkg"):
                saw_list = True
                count_from_list += 1
                sev = (
                    child.get("severity")
                    or child.get("type")
                    or child.get("category")
                    or ""
                ).strip().lower()
                if "security" in sev:
                    security_from_list += 1
        if saw_list:
            pending = count_from_list
            if security == 0:
                security = security_from_list
    # Secondary element-style: single <count> / <security> tags.
    if pending == 0:
        for child in root:
            tag = child.tag.lower()
            if tag in ("count", "update_count", "total") and child.text:
                try:
                    pending = int(float(child.text.strip()))
                except ValueError:
                    pass
            if tag in ("security", "security_count") and child.text:
                try:
                    security = int(float(child.text.strip()))
                except ValueError:
                    pass
    return {
        "host_updates_pending":  max(0, pending),
        "host_updates_security": max(0, security),
    }


_EXCLUDED_FSTYPES = {
    "tmpfs", "devtmpfs", "squashfs", "overlay", "overlay2", "aufs",
    "fuse.gvfsd-fuse", "fuse.lxcfs", "nsfs", "proc", "sysfs", "cgroup",
    "cgroup2", "ramfs", "rpc_pipefs", "mqueue", "devpts", "securityfs",
    "configfs", "debugfs", "hugetlbfs", "pstore", "tracefs", "autofs",
    "binfmt_misc", "fusectl", "bpf",
}

_EXCLUDED_MOUNT_PREFIXES = (
    "/proc", "/sys", "/dev", "/run",
    "/var/lib/docker", "/var/lib/containerd", "/var/lib/kubelet",
    "/snap/", "/var/snap",
)


def extract_mounts(root: ET.Element) -> list[dict]:
    """Shape the ``mount?xml=1`` response into the OmniGrid mounts list.

    Returns a list of ``{n, d, du, dp, dr, dw}`` entries where ``d`` and
    ``du`` are in GiB (floats — matches Beszel's extra filesystems so
    the UI iterates one schema). Filters pseudo-fs and Docker dirs.
    """
    if root is None:
        return []
    gib = 1024 ** 3
    out: list[dict] = []
    for node in root.iter():
        tag = node.tag.lower()
        if tag not in ("mount", "filesystem", "fs", "disk"):
            continue
        mount = (
            node.get("dir")
            or node.get("mountpoint")
            or node.get("mount_point")
            or node.get("path")
            or ""
        ).strip()
        fstype = (
            node.get("fstype")
            or node.get("type")
            or ""
        ).strip()
        if not mount:
            continue
        if fstype and fstype.lower() in _EXCLUDED_FSTYPES:
            continue
        if any(mount.startswith(p) for p in _EXCLUDED_MOUNT_PREFIXES):
            continue
        size = _num(
            node.get("size_bytes")
            or node.get("size")
            or node.get("total")
            or node.get("total_bytes")
        )
        used = _num(
            node.get("used_bytes")
            or node.get("used")
        )
        avail = _num(
            node.get("avail_bytes")
            or node.get("avail")
            or node.get("free")
            or node.get("free_bytes")
        )
        if size <= 0 and (used > 0 or avail > 0):
            size = used + avail
        if used <= 0 and size > 0 and avail > 0:
            used = max(0.0, size - avail)
        if size <= 0:
            continue
        pct = (used / size * 100) if size > 0 else 0.0
        out.append({
            "n":  mount,
            "d":  size / gib,
            "du": used / gib,
            "dp": pct,
            "dr": 0,
            "dw": 0,
            "fstype": fstype,
        })
    out.sort(key=lambda m: m.get("dp", 0), reverse=True)
    return out


def extract_net_ifaces(root: ET.Element) -> list[dict]:
    """Shape the ``net?xml=1`` response into the OmniGrid NIC list.

    Returns ``[{name, mac, addrs: []}, ...]`` matching Beszel / Pulse.
    """
    if root is None:
        return []
    out: list[dict] = []
    for node in root.iter():
        tag = node.tag.lower()
        if tag not in ("interface", "iface", "net", "netif", "nic"):
            continue
        name = (node.get("name") or node.get("iface") or "").strip()
        if not name:
            continue
        mac = (
            node.get("mac")
            or node.get("hwaddr")
            or node.get("mac_address")
            or ""
        ).strip()
        addrs: list[str] = []
        primary = (
            node.get("address")
            or node.get("ip")
            or node.get("ipv4")
            or ""
        ).strip()
        if primary:
            addrs.append(primary)
        v6 = (node.get("ipv6") or node.get("ip6") or "").strip()
        if v6:
            addrs.append(v6)
        for child in node:
            ctag = child.tag.lower()
            if ctag in ("address", "ip", "ipv4", "ipv6", "addr"):
                val = (child.text or child.get("value") or "").strip()
                if val and val not in addrs:
                    addrs.append(val)
        out.append({
            "name":  name,
            "mac":   mac,
            "addrs": addrs,
        })
    return out


def extract_stats(
    system_status: Optional[dict],
    package_updates: Optional[dict],
    mounts: Optional[list],
    net_ifaces: Optional[list],
    active_sources: Optional[set[str]] = None,
) -> dict:
    """Compose the four per-module extractors into one host_* dict.

    ``active_sources`` is the set of CURRENTLY enabled providers. We use
    it to suppress ``host_cpu_percent`` when Beszel / node-exporter are
    in the chain — their longer-window CPU readings are smoother than
    Webmin's one-second ``/proc/stat`` snapshot.
    """
    stats: dict = {}
    if system_status:
        stats.update(system_status)
    if package_updates:
        stats.update(package_updates)
    if mounts is not None:
        stats["mounts"] = mounts
        total = 0.0
        used = 0.0
        for m in mounts:
            total += _num(m.get("d"))
            used += _num(m.get("du"))
        if total > 0:
            gib = 1024 ** 3
            stats["host_disk_total"] = int(total * gib)
            stats["host_disk_used"] = int(used * gib)
            stats["host_disk_free"] = max(0, int((total - used) * gib))
            stats["host_disk_percent"] = (used / total * 100) if total > 0 else 0.0
    if net_ifaces is not None:
        stats["network_ifaces"] = net_ifaces
    others = (active_sources or set()) - {"webmin"}
    if others & {"beszel", "node_exporter", "pulse"}:
        stats.pop("host_cpu_percent", None)
    stats["exporter_error"] = None
    return stats


async def probe_webmin(
    base_url: str,
    user: str,
    password: str,
    verify_tls: bool = True,
    timeout: float = 15.0,
    active_sources: Optional[set[str]] = None,
) -> dict:
    """Fetch a single Webmin host's four Phase-1 modules in parallel.

    Returns ``{"hosts": {host_key: stats}, "error": None}`` on success
    or ``{"hosts": {}, "error": "..."}`` on any failure. Never raises.

    Unlike Beszel / Pulse (each of which hits one hub that enumerates
    every host), Webmin is per-host — one Miniserv instance per target
    box. ``probe_webmin`` therefore probes ONE host per call; the caller
    (``gather.py`` / ``api_hosts``) iterates curated rows and fans out.
    """
    if not base_url or not user or not password:
        return {"hosts": {}, "error": "webmin: missing url / user / password"}
    cd = _in_cooldown(base_url, user)
    if cd is not None:
        return {
            "hosts": {},
            "error": f"webmin: auth cool-down ({int(cd)}s remaining) — "
                     f"check credentials and wait before retrying",
        }
    base = base_url.rstrip("/")
    paths = {
        "system_status":   "/system-status/?xml=1",
        "package_updates": "/package-updates/?xml=1&mode=count",
        "mount":           "/mount/?xml=1",
        "net":             "/net/?xml=1",
    }
    try:
        # Two-stage auth: session-login first (default Miniserv behaviour),
        # then Basic auth as fallback for hosts with no_session=1. The
        # client starts WITHOUT Authorization so the /session_login.cgi
        # POST isn't short-circuited by a Basic header Miniserv doesn't
        # accept for the login endpoint itself.
        async with httpx.AsyncClient(
            verify=verify_tls,
            timeout=timeout,
            follow_redirects=True,
        ) as client:
            logged_in = await _session_login(client, base, user, password)
            if not logged_in:
                # Fallback — operators with no_session=1 have functional
                # Basic auth. Attach and proceed; _fetch_xml still
                # reports the "got HTML" signal cleanly if that fails too.
                client.auth = httpx.BasicAuth(user, password)
            results = await asyncio.gather(*(
                _fetch_xml(client, base, p, user) for p in paths.values()
            ), return_exceptions=False)
    except Exception as e:
        return {"hosts": {}, "error": f"webmin: {e}"}

    # Name-align results with their module keys.
    by_mod = dict(zip(paths.keys(), results))
    errors: list[str] = []
    roots: dict[str, Optional[ET.Element]] = {}
    for mod, (root, err) in by_mod.items():
        roots[mod] = root
        if err:
            errors.append(f"{mod}: {err}")

    # If EVERY module failed, surface the aggregate — helpful when the
    # operator mistyped the URL or Basic isn't whitelisted.
    if all(r is None for r in roots.values()):
        return {"hosts": {}, "error": "; ".join(errors) or "webmin: all modules failed"}

    system_status = extract_system_status(roots["system_status"])
    package_updates = extract_package_updates(roots["package_updates"])
    mounts = extract_mounts(roots["mount"])
    net_ifaces = extract_net_ifaces(roots["net"])
    stats = extract_stats(
        system_status, package_updates, mounts, net_ifaces,
        active_sources=active_sources,
    )
    stats["webmin_name"] = system_status.get("host_hostname") or ""
    stats["webmin_errors"] = errors

    host_key = stats["webmin_name"] or base_url
    print(f"[webmin] probe: url={base_url!r} user={user!r} "
          f"host_key={host_key!r} updates={stats.get('host_updates_pending')} "
          f"security={stats.get('host_updates_security')} "
          f"mounts={len(mounts)} nics={len(net_ifaces)} "
          f"errors={len(errors)}")
    if errors:
        print(f"[webmin] probe: partial errors: {errors}")
    return {
        "hosts":   {host_key: stats} if host_key else {},
        "error":   None if not errors or stats else "; ".join(errors),
        "partial_errors": errors,
    }


def lookup(webmin_hosts: dict, needle: str) -> Optional[dict]:
    """Case / whitespace-tolerant key lookup. Same signature as the
    Beszel / Pulse helpers so the merge-site code can swap providers
    without branch-specific matchers."""
    if not webmin_hosts or not needle:
        return None
    if needle in webmin_hosts:
        return webmin_hosts[needle]
    key = needle.strip().lower()
    if not key:
        return None
    for k, v in webmin_hosts.items():
        if k.strip().lower() == key:
            return v
    return None
