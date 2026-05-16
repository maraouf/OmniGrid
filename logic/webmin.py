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

Output-format fallback (per module): XML → JSON → HTML scrape. Webmin
2.x silently dropped ``?xml=1`` on package-updates / mount / net, then
2.630 went further and dropped ``?json=1`` on the same modules. The
three-tier dispatch in ``_fetch_first_working`` keeps the host card
populated even on the worst builds. The JSON branch converts dicts to
``ET.Element`` trees so downstream extractors don't care about format;
the HTML branch uses BeautifulSoup to parse tables when neither query
param works. ``system-status`` always honours ``?xml=1`` in practice,
but the fallbacks are wired there too so a future regression doesn't
need a new code path.

Auth: HTTP Basic with a dedicated read-only Webmin user. This sidesteps
the session-cookie + CSRF dance; the operator enables Basic for the
API user via ``no_session=<user>=<name>`` in ``/etc/webmin/miniserv.conf``.

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
import json as _json
import time
import re
from typing import Optional
from xml.etree import ElementTree as ET

import httpx

# BeautifulSoup powers the LAST-RESORT HTML scrape path. Import is
# lazy-optional so a dev machine without bs4 still boots — the scrape
# branch simply returns an error when bs4 is missing and callers fall
# back to "primary XML/JSON errors" reporting.
try:
    from bs4 import BeautifulSoup  # type: ignore
    _HAS_BS4 = True
except ImportError:
    BeautifulSoup = None  # type: ignore
    _HAS_BS4 = False


# cool-down duration shared with logic/ssh.py via
# `tuning_auth_failure_cooldown_seconds` (default 300, range 5-3600).
# Per-(base_url, user) key avoids locking out global creds when ONE
# Miniserv has stale auth. The Cooldown timer's seconds parameter
# reads `tuning_int(...)` lazily on every `arm()` / `remaining()`
# call (see logic/cooldown.py) so the operator's Save in Admin →
# Config takes effect on the next probe without a restart.
from logic.cooldown import Cooldown as _Cooldown
from logic.merge import normalize_arch as _normalize_arch
from logic import tuning as _tuning
from logic.tuning import Tunable as _Tunable
_auth_cooldown_timer = _Cooldown(
    seconds_fn=lambda: _tuning.tuning_int(_Tunable.AUTH_FAILURE_COOLDOWN_SECONDS)
)


# Threat model for the URL parameter: ``base_url`` is operator-set
# via the admin-only ``/api/settings`` endpoint (require_admin gate +
# CSRF) — it is NOT public-facing input. Defence-in-depth + CodeQL
# suppression rationale lives in ``logic/url_safety.py``; the validator
# below is the alias every probe module uses.
from logic.url_safety import is_safe_http_url as _validate_webmin_url

# Plural → singular for _json_to_element's list wrapping. Webmin JSON
# responses use plural keys for arrays ("mounts", "updates") but the
# XML-based extractors iterate looking for singular tags ("mount",
# "update"). Centralising the mapping avoids 3× duplicate logic.
_SINGULAR_TAG = {
    "interfaces": "interface",
    "ifaces":     "iface",
    "mounts":     "mount",
    "filesystems": "filesystem",
    "disks":      "disk",
    "updates":    "update",
    "packages":   "package",
    "pkgs":       "pkg",
}


def _in_cooldown(base_url: str, user: str) -> Optional[float]:
    """Return remaining cool-down seconds (>0) if we're still backing
    off from a recent 401, or ``None`` when the probe can proceed."""
    return _auth_cooldown_timer.remaining(base_url.rstrip("/"), user or "")


def _arm_cooldown(base_url: str, user: str) -> None:
    _auth_cooldown_timer.arm(base_url.rstrip("/"), user or "")


async def _session_login(
    client: httpx.AsyncClient,
    base_url: str,
    user: str,
    password: str,
) -> bool:
    """Establish a Miniserv session via ``/session_login.cgi``.

    Miniserv's cookie-based auth requires a two-step round-trip:

      1. **GET** the login page. Miniserv sets a ``testing=1`` cookie
         to verify the client accepts cookies at all. Without this
         cookie on the POST, Miniserv rejects the login "to stop
         brute-force attacks".
      2. **POST** credentials. httpx auto-replays the testing cookie
         from step 1; Miniserv validates and sets a fresh ``sid=<hex>``
         cookie on success (or leaves a ``sid=x`` placeholder on
         failure).

    Returns True when a real, non-placeholder session cookie is
    present after step 2. Verbose ``[webmin] session_login`` logs on
    every outcome so operators can diagnose via Admin → Logs when
    Basic-auth fallback doesn't rescue the probe either.
    """
    # ``base_url`` is admin-set (not public input) and validated by
    # ``_validate_webmin_url`` at the probe_webmin entry point. The
    # CodeQL py/full-ssrf flag on the GET/POST below is a false positive
    # for OmniGrid's threat model — see the module-level note next to
    # ``_ALLOWED_SCHEMES``.
    login_url = base_url.rstrip("/") + "/session_login.cgi"
    # Step 1 — GET to arm the testing cookie. Miniserv may send this
    # cookie on the login page body; we don't care about the body
    # itself, only that httpx captures the Set-Cookie.
    try:
        r1 = await client.get(  # lgtm[py/full-ssrf]
            login_url,
            headers={"Referer": base_url.rstrip("/") + "/"},
        )
        print(f"[webmin] session_login GET {login_url} -> {r1.status_code}, "
              f"cookies after GET: {dict(client.cookies)}")
    except Exception as e:
        print(f"[webmin] session_login GET {login_url} failed: {e}")
        return False

    # Step 2 — POST credentials. Include ``page=/`` so Miniserv knows
    # where to redirect on success; without it some versions return a
    # bare "login successful" page and skip setting the cookie on
    # subsequent redirects.
    try:
        r2 = await client.post(  # lgtm[py/full-ssrf]
            login_url,
            data={"user": user, "pass": password, "save": "1", "page": "/"},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": login_url,
            },
        )
        print(f"[webmin] session_login POST {login_url} -> {r2.status_code}, "
              f"cookies after POST: {dict(client.cookies)}")
    except Exception as e:
        print(f"[webmin] session_login POST {login_url} failed: {e}")
        return False

    # Miniserv sets various cookie names across versions — ``sid`` is
    # canonical, ``sessid`` / ``webmin`` appear on older builds and
    # some reverse-proxied setups (NPM strips some headers). Accept
    # any of them unless they hold the ``x`` logout placeholder.
    for name, value in (client.cookies or {}).items():
        lname = (name or "").lower()
        if lname in ("sid", "sessid", "webmin"):
            if value and value.lower() not in ("", "x"):
                print(f"[webmin] session_login SUCCESS — cookie {name}={value[:8]}…")
                return True
            print(f"[webmin] session_login received placeholder cookie {name}={value!r}")

    # Diagnostic: if the response body looks like a login form again,
    # the credentials were likely rejected. Log the page title so the
    # operator can spot "Access denied" / "Too many failed logins".
    body = (r2.text or "").lstrip()
    if body.lower().startswith(("<!doctype", "<html")):
        hint = _strip_html(body)
        print(f"[webmin] session_login REJECTED — body looks like HTML: {hint!r}")
    else:
        print(f"[webmin] session_login returned non-HTML body ({len(body)} bytes) but no session cookie")
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
        r = await client.get(url)  # lgtm[py/full-ssrf]
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
    # Keep a reference to the RAW body before any transforms — the
    # operator needs to see exactly what the wire returned when a
    # parse fails, including BOMs, duplicate XML declarations, or
    # stray Content-Type-in-body bytes that the BOM strip missed.
    raw_body = r.text or ""
    if not raw_body.strip():
        return None, f"{path}: empty response"
    body = raw_body
    # Strip a BOM that some Webmin 2.x builds emit ahead of XML
    # declarations. ElementTree's parser rejects a leading BOM as
    # "not well-formed (invalid token): line 2, column 16" — the
    # BOM sits before ``<?xml ...?>\n<root>`` which trips the parser
    # at the second line's first real element.
    if body.startswith("﻿"):
        body = body[1:]
    # Webmin sometimes returns a login HTML page for unauthenticated
    # probes when Basic isn't whitelisted for the user. Detect the
    # tell-tale ``<html`` prefix and surface a cleaner error. The
    # ``<title>`` tells us which page we actually got (login vs.
    # the full HTML UI page which Webmin 2.x returns when ``?xml=1``
    # isn't recognised for a module).
    stripped = body.lstrip().lower()
    if stripped.startswith("<!doctype html") or stripped.startswith("<html"):
        hint = _strip_html(body)
        return None, (f"{path}: expected XML, got HTML"
                      + (f" — {hint}" if hint else ""))
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        # Verbose diagnostic dump — the operator has been seeing
        # "line 2 col 16" errors even after the BOM strip. Surface
        # enough of the actual bytes to identify what the BOM strip
        # missed: raw content-type header, duplicate <?xml?>
        # declaration, stray whitespace, etc. Prints BOTH the raw
        # repr (shows BOMs / control chars as \uXXXX) AND the hex
        # of the first 32 bytes (catches invisible Unicode).
        raw_preview = raw_body[:200].replace("\n", "\\n").replace("\r", "\\r")
        stripped_preview = body[:200].replace("\n", "\\n").replace("\r", "\\r")
        try:
            raw_bytes = raw_body.encode("utf-8", errors="replace")[:32]
            hex_preview = raw_bytes.hex(" ")
        except Exception:  # noqa: BLE001
            hex_preview = "<encode failed>"
        ctype = r.headers.get("content-type", "?")
        print(
            f"[webmin] XML parse error for {url}: {e}\n"
            f"[webmin]   content-type: {ctype!r}\n"
            f"[webmin]   raw[:200]:      {raw_preview!r}\n"
            f"[webmin]   stripped[:200]: {stripped_preview!r}\n"
            f"[webmin]   raw_hex[:32]:   {hex_preview}"
        )
        return None, f"{path}: XML parse error — {e}"
    return root, None


def _json_to_element(data, tag: str = "root") -> ET.Element:
    """Convert a parsed JSON value to an ``ET.Element`` tree.

    Scalar dict values become attributes of the parent element; dict /
    list values become child elements. Lists are wrapped so each item
    gets a singular tag (e.g. a JSON ``"mounts": [...]`` array ends up
    as ``<mounts><mount .../><mount .../></mounts>``). Makes JSON
    responses feed directly into the existing XML-based extractors
    without a parallel code path.
    """
    el = ET.Element(tag)
    if isinstance(data, dict):
        for k, v in data.items():
            key = str(k)
            if isinstance(v, (dict, list)):
                el.append(_json_to_element(v, key))
            elif v is None:
                continue
            else:
                # ET.Element.set() requires a string — coerce scalars.
                el.set(key, str(v))
    elif isinstance(data, list):
        singular = _SINGULAR_TAG.get(tag.lower())
        if not singular:
            if tag.endswith("ies") and len(tag) > 3:
                singular = tag[:-3] + "y"
            elif tag.endswith("es") and len(tag) > 2 and not tag.endswith("ses"):
                singular = tag[:-2]
            elif tag.endswith("s") and len(tag) > 1:
                singular = tag[:-1]
            else:
                singular = tag + "_item"
        for item in data:
            el.append(_json_to_element(item, singular))
    else:
        el.text = "" if data is None else str(data)
    return el


async def _fetch_json(
    client: httpx.AsyncClient,
    base_url: str,
    path: str,
    user: str,
) -> tuple[Optional[ET.Element], Optional[str]]:
    """GET ``base_url + path`` and parse the response as JSON.

    Returns ``(element_tree, None)`` on success — the JSON payload is
    converted to an ``ET.Element`` so downstream extractors (which were
    written against XML) can walk it without branching on format.
    ``(None, error)`` on any failure. Arms the auth cool-down on 401.
    """
    url = base_url.rstrip("/") + path
    try:
        r = await client.get(url)  # lgtm[py/full-ssrf]
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
    stripped = body.lstrip().lower()
    if stripped.startswith("<!doctype html") or stripped.startswith("<html"):
        hint = _strip_html(body)
        return None, (f"{path}: expected JSON, got HTML"
                      + (f" — {hint}" if hint else ""))
    try:
        data = _json.loads(body)
    except _json.JSONDecodeError as e:
        preview = body[:200].replace("\n", "\\n").replace("\r", "\\r")
        print(f"[webmin] JSON parse error for {url}: {e}; body preview: {preview!r}")
        return None, f"{path}: JSON parse error — {e}"
    return _json_to_element(data, "root"), None


def _parse_bytes(text: str) -> int:
    """Convert a human-readable byte string (e.g. ``"10.5 GB"``) to bytes.

    Handles SI (``KB``/``MB``/``GB``) and IEC (``KiB``/``MiB``/``GiB``)
    suffixes identically — Webmin is inconsistent and the small precision
    difference (1000 vs 1024) is dwarfed by the normal read-out noise.
    Returns 0 on anything unparseable.
    """
    if not text:
        return 0
    s = text.strip().upper()
    # Handle comma thousand-separators AND european comma-decimals by
    # stripping only when there's a matching dot.
    if "," in s and "." in s:
        s = s.replace(",", "")
    m = re.match(r"([\d.,]+)\s*([KMGTPE])?I?B?", s)
    if not m:
        return 0
    try:
        val = float(m.group(1).replace(",", "."))
    except ValueError:
        return 0
    unit = m.group(2) or ""
    scale = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3,
             "T": 1024**4, "P": 1024**5, "E": 1024**6}.get(unit, 1)
    return int(val * scale)


def _scrape_package_updates(soup) -> Optional[ET.Element]:
    """Extract pending-update counters from Webmin's package-updates HTML.

    Webmin's summary line above the updates table usually reads
    ``"19 packages can be updated"`` / ``"12 are security updates"``.
    Primary strategy matches those patterns; secondary falls back to
    counting rows in the updates table (one <tr> per package, with a
    "security" hint in the severity column).
    """
    root = ET.Element("root")
    pending = 0
    security = 0
    text = soup.get_text(" ", strip=True)
    for pat in (
        r"(\d+)\s+packages?\s+(?:need|require|can be|to be)\s+updat",
        r"(\d+)\s+update[s]?\s+(?:available|pending)",
        r"(?:total|pending)[:\s]+(\d+)",
    ):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                pending = int(m.group(1))
                break
            except ValueError:
                pass
    m = re.search(r"(\d+)\s+(?:are\s+)?security\s+update", text, re.IGNORECASE)
    if m:
        try:
            security = int(m.group(1))
        except ValueError:
            pass
    if pending == 0:
        best_count = 0
        best_security = 0
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            data_rows = [r for r in rows if r.find("td") and not r.find("th")]
            if not data_rows:
                continue
            count = 0
            sec = 0
            for r in data_rows:
                cells = r.find_all("td")
                if len(cells) < 3:
                    continue
                count += 1
                row_text = r.get_text(" ", strip=True).lower()
                if "security" in row_text:
                    sec += 1
            if count > best_count:
                best_count = count
                best_security = sec
        if best_count > 0:
            pending = best_count
            if security == 0:
                security = best_security
    root.set("total", str(pending))
    root.set("security", str(security))
    return root


def _scrape_mounts(soup) -> Optional[ET.Element]:
    """Extract the mount list from Webmin's mount/disk HTML UI.

    Walks every ``<table>`` looking for a header row that names both
    "Mount" / "Directory" / "Path" AND "Size" / "Total" / "Space",
    then parses each subsequent row. Column indices are derived from
    the header; unknown columns are ignored. Skips pseudo-filesystems
    the same way the XML extractor does so a scrape-driven host has
    an identical output shape.
    """
    root = ET.Element("root")
    for table in soup.find_all("table"):
        header = table.find("tr")
        if not header:
            continue
        hcells = [c.get_text(" ", strip=True).lower()
                  for c in header.find_all(["th", "td"])]
        if not hcells:
            continue
        has_mount = any("mount" in h or "direct" in h or "path" in h for h in hcells)
        has_size = any("size" in h or "total" in h or "space" in h for h in hcells)
        if not (has_mount and has_size):
            continue
        mount_ix = next((i for i, h in enumerate(hcells)
                         if "mount" in h or "direct" in h or "path" in h), None)
        size_ix = next((i for i, h in enumerate(hcells)
                        if "size" in h or "total" in h or "space" in h), None)
        used_ix = next((i for i, h in enumerate(hcells) if "used" in h), None)
        avail_ix = next((i for i, h in enumerate(hcells)
                         if "avail" in h or "free" in h), None)
        fstype_ix = next((i for i, h in enumerate(hcells)
                          if h.startswith("type") or "fstype" in h or "fs " in h), None)
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all("td")
            if not cells or mount_ix is None or size_ix is None:
                continue
            if mount_ix >= len(cells) or size_ix >= len(cells):
                continue
            mount = cells[mount_ix].get_text(" ", strip=True)
            size_txt = cells[size_ix].get_text(" ", strip=True)
            if not mount or not size_txt:
                continue
            size = _parse_bytes(size_txt)
            if size <= 0:
                continue
            used = (_parse_bytes(cells[used_ix].get_text(" ", strip=True))
                    if used_ix is not None and used_ix < len(cells) else 0)
            avail = (_parse_bytes(cells[avail_ix].get_text(" ", strip=True))
                     if avail_ix is not None and avail_ix < len(cells) else 0)
            fstype = (cells[fstype_ix].get_text(" ", strip=True)
                      if fstype_ix is not None and fstype_ix < len(cells) else "")
            m = ET.SubElement(root, "mount")
            m.set("dir", mount)
            m.set("size_bytes", str(size))
            if used:
                m.set("used_bytes", str(used))
            if avail:
                m.set("avail_bytes", str(avail))
            if fstype:
                m.set("fstype", fstype)
    return root


def _scrape_net(soup) -> Optional[ET.Element]:
    """Extract the NIC list from Webmin's net/ifconfig HTML UI.

    Same heuristic shape as ``_scrape_mounts`` — find every table whose
    header names "Name"/"Interface"/"Device", then pull the address
    and MAC columns by header match. Webmin 2.x sometimes splits
    physical / virtual / VLAN NICs across multiple tables under
    separate ``<h3>`` sections; — walk every matching
    table and de-dup by NIC name (first-seen wins for the IP / MAC
    columns) so the operator's drawer shows the union, not just the
    physical list. The output is a sequence of
    ``<interface name="..." address="..." mac="..."/>`` elements that
    the XML extractor can walk unchanged.
    """
    root = ET.Element("root")
    seen: set[str] = set()
    for table in soup.find_all("table"):
        header = table.find("tr")
        if not header:
            continue
        hcells = [c.get_text(" ", strip=True).lower()
                  for c in header.find_all(["th", "td"])]
        if not hcells:
            continue
        has_iface = any("name" in h or "interface" in h or "device" in h
                        for h in hcells)
        if not has_iface:
            continue
        name_ix = next((i for i, h in enumerate(hcells)
                        if "name" in h or "interface" in h or "device" in h), None)
        ip_ix = next((i for i, h in enumerate(hcells)
                      if "address" in h or "ip" in h), None)
        mac_ix = next((i for i, h in enumerate(hcells)
                       if "mac" in h or "hardware" in h or "hwaddr" in h), None)
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all("td")
            if name_ix is None or name_ix >= len(cells):
                continue
            name = cells[name_ix].get_text(" ", strip=True)
            if not name or name in seen:
                # First-seen wins so a later boot-time-NIC table can't
                # overwrite the runtime IP we already captured.
                continue
            seen.add(name)
            ip = (cells[ip_ix].get_text(" ", strip=True)
                  if ip_ix is not None and ip_ix < len(cells) else "")
            mac = (cells[mac_ix].get_text(" ", strip=True)
                   if mac_ix is not None and mac_ix < len(cells) else "")
            iface = ET.SubElement(root, "interface")
            iface.set("name", name)
            if ip:
                iface.set("address", ip)
            if mac:
                iface.set("mac", mac)
    return root


def _scrape_system_status(soup) -> Optional[ET.Element]:
    """Best-effort extraction of system-status HTML.

    Rarely needed — ``system-status`` honours ``?xml=1`` on every
    Webmin release we've seen. Included for completeness so a future
    2.7 regression on that module doesn't force a new code path.
    """
    root = ET.Element("root")
    text = soup.get_text(" ", strip=True)
    m = re.search(r"(?:Hostname|System hostname)[:\s]+([\w.\-]+)", text, re.IGNORECASE)
    if m:
        root.set("hostname", m.group(1).strip())
    m = re.search(r"Kernel(?:\s+version)?[:\s]+(\S+\s+\S+)", text, re.IGNORECASE)
    if m:
        root.set("kernel", m.group(1).strip())
    m = re.search(r"(?:Operating system|Distribution)[:\s]+([^\n]{2,80})", text, re.IGNORECASE)
    if m:
        root.set("os", m.group(1).strip())
    return root


async def _fetch_html_scrape(
    client: httpx.AsyncClient,
    base_url: str,
    path: str,
    user: str,
    module: str,
) -> tuple[Optional[ET.Element], Optional[str]]:
    """Fetch the HTML UI at ``path`` and run the module's scraper.

    Only invoked by ``_fetch_first_working`` when every XML AND JSON
    variant has failed. Silently no-ops when bs4 isn't importable so
    a dev install without the scrape dep still probes cleanly (errors
    get reported via the usual attempt-list).
    """
    if not _HAS_BS4:
        return None, f"{path}: bs4 unavailable — pip install beautifulsoup4"
    url = base_url.rstrip("/") + path
    try:
        r = await client.get(url)  # lgtm[py/full-ssrf]
    except Exception as e:
        return None, f"{path}: {e}"
    if r.status_code == 401:
        _arm_cooldown(base_url, user)
        return None, f"{path}: HTTP 401 — cool-down armed"
    if r.status_code >= 400:
        return None, f"{path}: HTTP {r.status_code}"
    body = r.text or ""
    if not body.strip():
        return None, f"{path}: empty response"
    stripped_head = body.lstrip().lower()[:600]
    if "login.cgi" in stripped_head and "password" in stripped_head:
        return None, f"{path}: HTML scrape got a login page"
    try:
        soup = BeautifulSoup(body, "html.parser")
    except Exception as e:
        return None, f"{path}: bs4 parse error — {e}"
    scrapers = {
        "system_status":   _scrape_system_status,
        "package_updates": _scrape_package_updates,
        "mount":           _scrape_mounts,
        "net":             _scrape_net,
    }
    fn = scrapers.get(module)
    if fn is None:
        return None, f"{path}: no scraper for module {module!r}"
    el = fn(soup)
    if el is None or len(list(el.iter())) <= 1:
        # Only the root element means the scraper didn't match anything
        # on the page. Treat as a miss so the error surfaces.
        return None, f"{path}: HTML scrape produced no data (patterns didn't match)"
    print(f"[webmin] HTML-scraped {module!r} at {url} — "
          f"{len(list(el.iter())) - 1} element(s) extracted")
    return el, None


async def _fetch_first_working(
    client: httpx.AsyncClient,
    base_url: str,
    paths: list[str],
    user: str,
    module: Optional[str] = None,
) -> tuple[Optional[ET.Element], Optional[str]]:
    """Try paths in parallel; return the first parseable result.

    Three-tier fallback:

      1. Fire every XML/JSON alternate in ``paths`` CONCURRENTLY and
         return the first one that parses. Remaining tasks are
         cancelled as soon as one succeeds. 401/403 on ANY task also
         short-circuits (no point retrying bad creds).
      2. If every structured attempt fails AND ``module`` is set AND
         bs4 is importable, re-walk only the paths that returned HTML
         (auth errors / 404s are skipped) and run the module's HTML
         scraper. First successful scrape wins. This phase stays
         sequential — it's a last-resort and usually only has 1-2
         viable paths.

    The parallelism matters: Webmin 2.630 ignores both ``?xml=1`` and
    ``?json=1`` on some modules so every structured alternate fails
    with "got HTML". Sequential cycling through 6-8 paths × 3-6s each
    = 20-40s per module; parallel = one slow response + cancel-rest.

    When every tier fails, the attempt list is collapsed into one
    readable error so Admin → Logs shows exactly what was tried.
    """
    # Split paths into structured (machine-readable query params) and
    # bare (HTML UI — only useful for the scrape phase below).
    structured_paths = [p for p in paths if ("xml=1" in p) or ("json=1" in p)]
    bare_paths = [p for p in paths if p not in structured_paths]

    attempts: list[str] = []
    html_path_candidates: list[str] = []
    auth_failure = False

    async def _dispatch(path: str):
        if "json=1" in path:
            root, err = await _fetch_json(client, base_url, path, user)
        else:
            root, err = await _fetch_xml(client, base_url, path, user)
        return path, root, err

    if structured_paths:
        tasks = [asyncio.create_task(_dispatch(p)) for p in structured_paths]
        try:
            for coro in asyncio.as_completed(tasks):
                path, root, err = await coro
                if root is not None:
                    if path != structured_paths[0]:
                        print(f"[webmin] {base_url}{structured_paths[0]} failed; "
                              f"succeeded via {path}")
                    # Cancel stragglers so we don't waste sockets on
                    # paths we no longer need.
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    return root, None
                attempts.append(f"{path}: {err}")
                if err and ("HTTP 401" in err or "HTTP 403" in err):
                    auth_failure = True
                    break  # short-circuit — skip scrape attempts too
                if err and "got HTML" in err:
                    html_path_candidates.append(path)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            # Let cancellations settle — otherwise pending Tasks can
            # log "Task was destroyed but it is pending" on shutdown.
            await asyncio.gather(*tasks, return_exceptions=True)

    if auth_failure:
        primary = attempts[0] if attempts else f"{paths[0]}: no response"
        if len(attempts) > 1:
            primary += f" (also tried {len(attempts) - 1} fallback path(s))"
        return None, primary

    # Last resort — HTML scrape. Bare paths come first (cheapest — no
    # query string side-effects), then paths that returned HTML during
    # the structured round (already confirmed 200-ok). Dedupe in case
    # a bare path was also tried structured.
    if module:
        scrape_candidates = list(dict.fromkeys(bare_paths + html_path_candidates))
        for path in scrape_candidates:
            root, err = await _fetch_html_scrape(
                client, base_url, path, user, module,
            )
            if root is not None:
                return root, None
            attempts.append(f"scrape {path}: {err}")

    primary = attempts[0] if attempts else f"{paths[0]}: no response"
    if len(attempts) > 1:
        primary += f" (also tried {len(attempts) - 1} fallback path(s))"
    return None, primary


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

    def pick_with_key(*names: str) -> tuple[str, str]:
        """Like ``pick`` but returns ``(value, matched_key)`` so callers
        can branch on which alias matched. Needed for memory-unit
        disambiguation
        — Webmin's `real_mem` is KiB, but `mem_total`/`memory_total`
        are sometimes already bytes on certain Webmin module variants;
        without the key the unit decision is impossible).
        """
        for sc in scopes:
            for name in names:
                v = _findtext(sc, name)
                if v:
                    return v, name
        return "", ""

    def _bytes_or_kib(value: float, key: str) -> int:
        """Return memory total in BYTES.

        ``real_mem`` is reliably KiB across every Webmin version we've
        seen — multiply by 1024. The alternate keys ``mem_total`` /
        ``memory_total`` are KiB on most builds but already bytes on
        some module variants; we disambiguate by magnitude. A real
        homelab / server box has at most a few TiB of RAM, which in
        KiB is at most ~10^10. A byte report of ≥ 2 GiB sits at 2^31
        already — well above any plausible KiB report. So: if the
        matched key is the byte-ambiguous alias AND the raw value
        exceeds 2^31, treat it as bytes; otherwise apply the KiB
        scaling.
        """
        if value <= 0:
            return 0
        if key == "real_mem":
            return int(value * 1024)
        # Heuristic: 2^31 (≈ 2.15 GiB) catches every realistic byte
        # report ≥ 2 GiB while staying far above any plausible KiB
        # report (a 2 TiB host's KiB count is ≈ 2 * 2^30 ≈ 2.15e9 —
        # right at the threshold, but real 2 TiB hosts running
        # Webmin are vanishingly rare; the trade-off favours
        # correctness on the common case).
        if value > (1 << 31):
            return int(value)
        return int(value * 1024)

    hostname = pick("hostname", "host", "name")
    kernel   = pick("kernel", "kernel_release", "release", "os_version")
    distro   = pick("distro", "os", "pretty_name", "os_name", "os_release")
    arch     = pick("arch", "architecture", "machine")
    cpu_type = pick("cpu_type", "cpu_model", "model", "cpu")
    cpus_raw = pick("cpus", "cores", "ncpus")
    cores    = int(_num(cpus_raw)) if cpus_raw else 0
    real_mem_raw, real_mem_key = pick_with_key("real_mem", "mem_total", "memory_total")
    real_mem = _num(real_mem_raw)
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

    mem_total_bytes = _bytes_or_kib(real_mem, real_mem_key)
    mem_used_bytes = 0
    if real_mem > 0 and free_mem >= 0:
        # Free / used must apply the SAME scaling as total. Use the
        # total's resolved unit by passing its matched key — when the
        # operator's Webmin emits both as bytes, we read both as
        # bytes; when both as KiB, we scale both. Webmin guarantees
        # the two values use the same unit (they come from the same
        # module on the same call).
        free_total_bytes = _bytes_or_kib(free_mem, real_mem_key)
        if free_total_bytes <= mem_total_bytes:
            mem_used_bytes = mem_total_bytes - free_total_bytes

    host_boot_ts = (time.time() - uptime_s) if uptime_s > 0 else None
    return {
        "host_hostname":   hostname,
        "host_kernel":     kernel,
        "host_os":         distro,
        "host_platform":   distro.split()[0] if distro else "",
        "host_arch":       _normalize_arch(arch),
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
    # Element-style: walk children and count or tally. scope
    # the walk to the first `<updates>` / `<packages>` / `<pkglist>`
    # parent's DIRECT children when one exists, so unrelated nested
    # elements with these tag names (operator's custom theme,
    # documentation blocks) can't inflate the count. Falls through to
    # the legacy root.iter() walk when no scoped parent is present —
    # Webmin variants that put rows directly under `<root>` keep
    # working unchanged.
    def _tally(iterable) -> tuple[int, int, bool]:
        count = 0
        sec = 0
        saw = False
        for child in iterable:
            tag = child.tag.lower()
            if tag in ("update", "package", "pkg"):
                saw = True
                count += 1
                sev = (
                    child.get("severity")
                    or child.get("type")
                    or child.get("category")
                    or ""
                ).strip().lower()
                if "security" in sev:
                    sec += 1
        return count, sec, saw

    if pending == 0:
        scoped_parent = None
        for parent in root.iter():
            if parent.tag.lower() in ("updates", "packages", "pkglist"):
                scoped_parent = parent
                break
        if scoped_parent is not None:
            count_from_list, security_from_list, saw_list = _tally(scoped_parent)
        else:
            count_from_list, security_from_list, saw_list = _tally(root.iter())
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
    timeout: float = 6.0,
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
    if not _validate_webmin_url(base_url):
        return {
            "hosts": {},
            "error": "webmin: invalid url — must be http:// or https:// with a hostname",
        }
    cd = _in_cooldown(base_url, user)
    if cd is not None:
        return {
            "hosts": {},
            "error": f"webmin: auth cool-down ({int(cd)}s remaining) — "
                     f"check credentials and wait before retrying",
            # Structured marker so callers can detect "this was a
            # cool-down skip vs a real failure" without substring
            # matching the message text. Per-(provider, host)
            # auto-pause counters check this to avoid counting cool-
            # down responses toward the threshold (the probe was
            # SKIPPED, not attempted).
            "skipped_cooldown": True,
        }
    base = base_url.rstrip("/")
    # Per-module alternate paths. Webmin 2.x (>= 2.000) silently
    # dropped ``?xml=1`` support on several modules — ``system-status``
    # still works, the others return the full HTML UI instead. Try a
    # ranked list so we catch the Webmin 1.x path first (cheap no-op on
    # new hosts that still accept it) and fall through to module-
    # specific ``list.cgi`` variants and the legacy ``acl.cgi`` / JSON
    # probes. First successful XML-parse wins.
    # Per-module alternate paths. Ranked cheapest-first: XML (native
    # OmniGrid format) before JSON (needs dict-to-Element conversion)
    # before bare HTML paths the scraper can hit. Webmin 2.630 is the
    # worst offender — honours neither ?xml=1 nor ?json=1 on these
    # three modules, so the trailing "bare" paths exist purely to give
    # _fetch_html_scrape something to walk once every structured
    # variant has failed. system-status doesn't need that treatment,
    # but keeping the shape uniform avoids a special-case dispatch.
    path_alternatives = {
        "system_status": [
            "/system-status/?xml=1",
            "/system-status/index.cgi?xml=1",
            "/system-status/?json=1",
            "/system-status/index.cgi?json=1",
            "/system-status/",
        ],
        "package_updates": [
            "/package-updates/?xml=1&mode=count",
            "/package-updates/?xml=1",
            "/package-updates/index.cgi?xml=1",
            "/package-updates/update.cgi?xml=1&search=1",
            "/package-updates/?json=1&mode=count",
            "/package-updates/?json=1",
            "/package-updates/index.cgi?json=1",
            "/package-updates/",
        ],
        "mount": [
            "/mount/?xml=1",
            "/mount/index.cgi?xml=1",
            "/mount/list_mounts.cgi?xml=1",
            "/mount/?json=1",
            "/mount/index.cgi?json=1",
            "/mount/list_mounts.cgi?json=1",
            "/mount/",
        ],
        "net": [
            "/net/?xml=1",
            "/net/index.cgi?xml=1",
            "/net/list_ifcs.cgi?xml=1",
            "/net/?json=1",
            "/net/index.cgi?json=1",
            "/net/list_ifcs.cgi?json=1",
            "/net/",
        ],
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
                _fetch_first_working(client, base, alts, user, module=mod)
                for mod, alts in path_alternatives.items()
            ), return_exceptions=False)
    except Exception as e:
        return {"hosts": {}, "error": f"webmin: {e}"}

    # Name-align results with their module keys.
    by_mod = dict(zip(path_alternatives.keys(), results))
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
