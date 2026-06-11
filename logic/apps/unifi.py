"""UniFi per-app module (UniFi Network / UniFi OS).

Wires a Ubiquiti UniFi console into the OmniGrid Apps surface following the
per-app contract (``gitsync.py`` / ``grafana.py`` shape):

    SLUGS               — catalog slugs this module handles
                          ("unifi" / "unifi-network" / "unifi-os").
    requires_api_key()  — True (the chip's ``api_key`` stores a UniFi API key —
                          UniFi site → Settings → Control Plane → Integrations →
                          "Create API Key". Treat it like a password; it grants
                          read + device-control access.)
    resolve_base_url(host_row, chip) -> str   (shared helper)
    test_credential(host_row, chip, candidate_key) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — status (read) + devices (read, rich list) + clients
                          (read) + restart-device (DESTRUCTIVE, arg).

Auth model: the OFFICIAL UniFi Network **Integration API** (UniFi OS 4.x /
Network application 9.0+). API-key auth via the ``X-API-KEY`` header — NOT the
legacy ``/api/login`` cookie + CSRF dance (deprecated for integrations). The
key lives in the chip's ``api_key`` field. The credential probe hits the
auth-required ``GET /proxy/network/integration/v1/sites`` so a bad / missing
key fails loudly (401). UniFi consoles ship a self-signed cert on the LAN, so
every client uses ``verify=False``. Single-instance app (NOT fleet). No image
proxy (no thumbnails).

The expanded card answers "is my UniFi fleet healthy right now":

    sites                         — site count on the console
    devices / online / offline    — adopted-device counts
    aps / switches / gateways      — best-effort device-type breakdown
    clients / wired / wireless     — connected-client counts
    version                        — Network application version (best-effort)

Upstream API reference: UniFi Network Integration API v1 — base
``<console-url>/proxy/network/integration/v1``, ``X-API-KEY`` header,
``GET /sites`` · ``GET /sites/{id}/devices`` · ``GET /sites/{id}/clients`` ·
``GET /info`` (version) · ``POST /sites/{id}/devices/{id}/actions``
``{"action": "RESTART"}``.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, fetch_gate, peek_cache, resolve_base_url, resolve_cache_ttl,
    resolve_credential_target)
from logic.coerce import as_dict, as_list, safe_int

# Catalog template slugs handled by this module. `unifi-os-server` is the
# canonical built-in template (a self-hosted UniFi OS Server / network
# controller); the bare `unifi` / `unifi-network` / `unifi-os` aliases cover an
# operator-renamed chip or a UDM / Cloud Key console pinned under a generic name.
SLUGS: tuple[str, ...] = ("unifi-os-server", "unifi", "unifi-network", "unifi-os")

# UniFi Network Integration API base path.
_API = "/proxy/network/integration/v1"

# Legacy UniFi Network controller API base path. The Integration API (v1) has NO
# WLAN-config endpoint, so the configured Wi-Fi-network (SSID) list is read from
# the classic controller API instead — the modern API key authenticates BOTH
# surfaces, so no separate session/cookie login is needed. See _fetch_wlan_names.
_LEGACY_API = "/proxy/network/api"

# Per-(host_id, service_idx) data cache for the expanded card. Default TTL
# overridable per chip via the editor's `cache_ttl` field. 30s — the console
# answers from memory so a short cache keeps the card live without hammering it.
DEFAULT_CACHE_TTL_S = 30
_data_cache: dict[str, tuple[float, dict]] = {}

# Cap on the rich-item rows a list skill returns + on sites fanned out per fetch
# (a home / small-biz console has 1 site; the cap is a runaway guard).
_MAX_ROWS = 25
_MAX_SITES = 8

# Device state → emoji for the rich device rows + AI text.
_STATE_EMOJI = {"online": "🟢", "offline": "🔴", "pending_adoption": "🟡",
                "adopting": "🟡", "provisioning": "🟡", "upgrading": "🔵",
                "heartbeat_missed": "🟠", "isolated": "🟠"}

# UniFi skills — three read-only + one destructive (arg). The arg-carrying
# restart skill surfaces to AI / Telegram only (a drawer button can't supply the
# device name); the read skills also render as one-click drawer buttons.
SKILLS: tuple[dict, ...] = (
    {
        "id": "unifi_status",
        "name": "UniFi status",
        "ai_phrases": ("unifi status, unifi overview, how many access points, are "
                       "my unifi devices online, unifi network summary, how many "
                       "clients on wifi, unifi health, ubiquiti status"),
        "destructive": False,
    },
    {
        "id": "unifi_devices",
        "name": "List UniFi devices",
        "ai_phrases": ("list unifi devices, show my access points, what switches do "
                       "i have, which unifi devices are offline, my unifi hardware, "
                       "list aps, unifi device status"),
        "destructive": False,
    },
    {
        "id": "unifi_clients",
        "name": "UniFi clients",
        "ai_phrases": ("how many clients are connected, unifi clients, wifi clients, "
                       "wired vs wireless clients, who is on my network, connected "
                       "devices count, top clients by usage, which client uses the "
                       "most bandwidth, busiest clients, who is using the most data"),
        "destructive": False,
    },
    {
        "id": "unifi_wlans",
        "name": "List UniFi Wi-Fi networks",
        "ai_phrases": ("list my wifi networks, what ssids do i have, show wireless "
                       "networks, name my wifi networks, what wlans are configured, "
                       "unifi ssids, list wifi"),
        "destructive": False,
    },
    {
        "id": "unifi_restart_device",
        "name": "Restart a UniFi device",
        "ai_phrases": ("restart the <name> ap, reboot the <name> switch, restart "
                       "<name> unifi device, reboot my <name> access point, "
                       "power-cycle <name>"),
        "arg": True,
        "arg_hint": "the UniFi device name (or MAC) to restart",
        "destructive": True,
    },
)


def requires_api_key() -> bool:
    """UniFi's Integration API authenticates every call via an API key; the
    editor MUST render the key input (stored in the chip's api_key) + Test."""
    return True


def _headers(key: str) -> dict:
    """UniFi Integration API key header + JSON Accept."""
    return {"X-API-KEY": key, "Accept": "application/json"}


# Device-type classification keywords. The Integration API's LIST endpoint
# returns the device ``model`` as a DISPLAY NAME ("U6 Pro", "AC Mesh", "USW Pro
# 24 PoE", "Dream Machine"), NOT a model code, and it omits the per-device
# ``interfaces`` / ``features`` (those need the per-device detail GET), so we
# classify by substring-matching the product-line name. Keywords are checked
# gateway → AP → switch (a UDM / Dream Router carries Wi-Fi + ports but is a
# gateway, so it must win first). Anything unmatched lands in "other".
_GATEWAY_TOKENS = ("udm", "udr", "udw", "uxg", "ucg", "usg", "uxr", "dream",
                   "gateway", "cloud gateway", "express")
_AP_TOKENS = ("u6", "u7", "uap", "ualr", "uwb", "u-lr", "ac mesh", "ac-mesh",
              "ac pro", "ac lite", "ac lr", "ac-lr", "ac edu", "ac hd", "ac shd",
              "ac iw", "ac in-wall", "mesh", "nanohd", "nano hd", "flexhd",
              "flex hd", "beaconhd", "beacon hd", "in-wall hd", "access point",
              "swiss army", "e7")
_SWITCH_TOKENS = ("usw", "switch", "flex mini", "flex 2.5", "flex utility",
                  "aggregation", "edgeswitch", "industrial")


def _classify_device(dev: dict) -> str:
    """Best-effort device bucket: ``"ap"`` / ``"switch"`` / ``"gateway"`` /
    ``"other"``. Substring-matches the device ``model`` display name against the
    product-line keyword sets (gateway wins first); falls back to the per-device
    ``interfaces`` shape (radios ⇒ AP, ports ⇒ switch) when present (detail GET
    only) and finally to the operator-authored ``name``. Imperfect by design —
    the reliable online/offline/total counts never depend on this."""
    model = str(dev.get("model") or "").strip().lower()
    name = str(dev.get("name") or "").strip().lower()
    ifaces = as_dict(dev.get("interfaces"))
    has_radios = bool(as_list(ifaces.get("radios")))
    has_ports = bool(as_list(ifaces.get("ports")))
    # 1) Model display-name keywords — gateway first (a UDM has Wi-Fi too).
    if any(t in model for t in _GATEWAY_TOKENS):
        return "gateway"
    if any(t in model for t in _AP_TOKENS):
        return "ap"
    if any(t in model for t in _SWITCH_TOKENS):
        return "switch"
    # 2) Interface shape (present only on the per-device detail, not the list).
    if has_radios:
        return "ap"
    if has_ports:
        return "switch"
    # 3) Operator-authored name as a last resort.
    if "gateway" in name or "dream" in name:
        return "gateway"
    if "switch" in name:
        return "switch"
    if "access point" in name or " ap" in (" " + name):
        return "ap"
    return "other"


def _client_is_wired(client: dict) -> bool:
    """True when a client connects over a wired port (vs Wi-Fi). The Integration
    API reports the medium as ``type`` and / or ``access.type``
    (``"WIRED"`` / ``"WIRELESS"``); also treat a present ``uplinkDeviceId`` +
    no radio as wired. Defaults to wireless when unknown (the common case)."""
    t = str(client.get("type") or as_dict(client.get("access")).get("type") or "").strip().upper()
    if t == "WIRED":
        return True
    if t == "WIRELESS":
        return False
    # Fallback: a wireless client carries radio / SSID info.
    if client.get("ssid") or client.get("radio") or client.get("wireless"):
        return False
    return False


async def _get_json(cli: "httpx.AsyncClient", url: str, key: str) -> Any:
    """GET a UniFi Integration endpoint and return parsed JSON, or None on any
    non-2xx / parse failure (caller decides how to degrade)."""
    r = await cli.get(url, headers=_headers(key))
    if not (200 <= r.status_code < 300):
        return None
    try:
        return r.json()
    except (ValueError, TypeError):
        return None


# The Integration API paginates EVERY list endpoint under a
# ``{offset, limit, count, totalCount, data:[...]}`` envelope and caps a page at
# `limit` records (default 25 — the source of the "only 25 clients shown when
# there are 59" truncation). Always page explicitly: request the max page size
# and walk `offset` until every record is collected (or a runaway guard trips).
_PAGE_LIMIT = 200
_MAX_PAGES = 50  # 200 × 50 = 10000-record ceiling — a hard runaway safety net.


async def _get_all(cli: "httpx.AsyncClient", url: str, key: str) -> list:
    """GET every page of a paginated Integration API list endpoint and return the
    merged ``data`` rows (dicts only). Walks ``offset`` until ``totalCount`` is
    reached / a short page lands / the page cap trips. ``[]`` on failure."""
    out: list = []
    offset = 0
    for _ in range(_MAX_PAGES):
        sep = "&" if "?" in url else "?"
        body = await _get_json(cli, f"{url}{sep}offset={offset}&limit={_PAGE_LIMIT}", key)
        d = as_dict(body)
        page = [x for x in as_list(d.get("data")) if isinstance(x, dict)]
        out.extend(page)
        total = safe_int(d.get("totalCount"))
        offset += len(page)
        if not page or len(page) < _PAGE_LIMIT or (total and offset >= total):
            break
    return out


async def _list_sites(cli: "httpx.AsyncClient", base: str, key: str) -> list:
    """All site records from ``GET /sites`` (paginated under a ``data``
    envelope). Returns ``[]`` on failure."""
    return await _get_all(cli, base + _API + "/sites", key)


async def _fetch_version(cli: "httpx.AsyncClient", base: str, key: str) -> str:
    """Best-effort Network application version from ``GET /info``; '' on miss.
    (The Integration API exposes the Network application version only — the
    console's UniFi-OS version is not part of this API surface.)"""
    body = await _get_json(cli, base + _API + "/info", key)
    info = as_dict(body)
    return str(info.get("applicationVersion") or info.get("version") or "").strip()


def _device_update_pending(dev: dict) -> bool:
    """True when the console reports a firmware update available for this device.
    The Integration API device shape varies by Network version, so probe the
    several plausible spellings defensively (top-level + nested under
    ``firmware``) — absent / falsy on every spelling ⇒ False (so the card simply
    doesn't claim updates when the API doesn't expose them)."""
    fw = as_dict(dev.get("firmware"))
    return bool(dev.get("firmwareUpdatable") or dev.get("firmwareUpdateAvailable")
                or dev.get("updateAvailable") or dev.get("upgradable")
                or dev.get("upgradeAvailable")
                or fw.get("updateAvailable") or fw.get("upgradable"))


# noinspection DuplicatedCode
async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe UniFi's auth-required ``GET /proxy/network/integration/v1/sites``
    with the supplied key. Returns ``{ok, detail, status}``. Falls back to the
    chip's stored ``api_key`` when ``candidate_key`` is blank so the operator can
    re-test after first save without retyping."""
    key, base, err = resolve_credential_target(host_row, chip, candidate_key)
    if err:
        return err
    url = base + _API + "/sites"
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, headers=_headers(key))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if r.status_code == 200:
        try:
            sites = as_list(as_dict(r.json()).get("data"))
        except (ValueError, TypeError):
            sites = []
        return {"ok": True, "detail": f"OK ({len(sites)} site(s))", "status": 200}
    if r.status_code in (401, 403):
        return {"ok": False,
                "detail": "auth failed (check the UniFi API key — Settings → "
                          "Control Plane → Integrations)",
                "status": r.status_code}
    if r.status_code == 404:
        return {"ok": False,
                "detail": "404 — the Integration API isn't reachable here (needs "
                          "UniFi OS 4.x / Network 9.0+ at the console URL)",
                "status": 404}
    return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}


def _client_ssid(client: dict) -> str:
    """The Wi-Fi network (SSID) a wireless client is on, '' for wired / unknown.
    Defensive over the field-name variants the Integration API uses across
    versions."""
    for k in ("ssid", "essid", "wlanName", "wlan", "networkName"):
        v = str(client.get(k) or "").strip()
        if v:
            return v
    acc = as_dict(client.get("access"))
    return str(acc.get("ssid") or acc.get("wlan") or "").strip()


def _wlan_name(wlan: dict) -> str:
    """The SSID / name of a WLANs-endpoint record, '' when absent. Defensive
    over the field-name variants across Network versions."""
    if not isinstance(wlan, dict):
        return ""
    for k in ("name", "ssid", "essid", "wlanName"):
        v = str(wlan.get(k) or "").strip()
        if v:
            return v
    return ""


async def _gather_site(cli: "httpx.AsyncClient", base: str, key: str,
                       site_id: str) -> "tuple[list, list, list]":
    """Fetch one site's devices + clients + WLANs in parallel, paginating each
    (the clients list routinely exceeds one page — that truncation was the "59
    clients but only 25 shown" bug). The WLANs endpoint isn't part of every
    Integration-API version, so ``_get_all`` returns ``[]`` for it on a 404 and
    the caller falls back to counting distinct client SSIDs. Returns
    ``(devices, clients, wlans)``."""
    devices, clients, wlans = await asyncio.gather(
        _get_all(cli, base + _API + f"/sites/{site_id}/devices", key),
        _get_all(cli, base + _API + f"/sites/{site_id}/clients", key),
        _get_all(cli, base + _API + f"/sites/{site_id}/wlans", key),
    )
    return devices, clients, wlans


async def _legacy_site_keys(cli: "httpx.AsyncClient", base: str, key: str) -> list[str]:
    """Internal legacy-controller site keys (e.g. ``default``) from the classic
    ``/proxy/network/api/self/sites`` endpoint — the path component the legacy
    ``/s/{site}/...`` routes need (distinct from the Integration API's site id).
    Falls back to ``['default']`` when the probe is unavailable. Shared by the
    legacy WLAN-config + per-client-usage walks."""
    sites_body = await _get_json(cli, base + _LEGACY_API + "/self/sites", key)
    keys = [str(s.get("name") or "").strip()
            for s in as_list(as_dict(sites_body).get("data"))
            if isinstance(s, dict) and s.get("name")]
    return keys or ["default"]


async def _fetch_wlan_names(cli: "httpx.AsyncClient", base: str, key: str) -> list[str]:
    """Configured Wi-Fi-network (SSID) names via the LEGACY UniFi Network
    controller API. The Integration API (v1) exposes no WLAN-config endpoint and
    its client records don't carry the SSID name, so neither the Integration
    ``/wlans`` route nor the distinct-client-SSID fallback can list the
    configured networks — but the classic controller API's ``rest/wlanconf``
    does, and the same ``X-API-KEY`` authenticates it (no cookie login needed).

    Walks every site's ``/proxy/network/api/s/{site}/rest/wlanconf`` (site keys
    from ``_legacy_site_keys``). Returns the sorted, de-duplicated
    (case-insensitive) names, ``[]`` on any failure so the caller degrades to
    the client-SSID heuristic."""
    from urllib.parse import quote  # noqa: PLC0415
    names: set[str] = set()
    for sk in (await _legacy_site_keys(cli, base, key))[:_MAX_SITES]:
        body = await _get_json(
            cli, base + _LEGACY_API + f"/s/{quote(sk, safe='')}/rest/wlanconf", key)
        for w in as_list(as_dict(body).get("data")):
            if isinstance(w, dict):
                nm = str(w.get("name") or "").strip()
                if nm:
                    names.add(nm)
    return sorted(names, key=str.lower)


def _fmt_bytes(n: Any) -> str:
    """Humanise a byte count (B / KB / MB / GB / TB, 1024-base, 1 decimal above
    bytes)."""
    v = float(max(0, safe_int(n)))
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024:
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} TB"


# Device-type categories keyed off the client's name / hostname / vendor text.
# ORDERED most-specific-first — first keyword hit wins, so e.g. "apple tv"
# (Multimedia) is tested before a bare "apple" would land it as a Laptop, and
# "nest hub" (Multimedia, a smart display) before "aqara ... hub" (IoT Hub).
# Returns a short bracket label for the clients-skill rows (📱 / 🔌 emoji still
# carries the wired/wireless axis separately).
_CLIENT_CATEGORIES: tuple = (
    ("Router", ("router", "rt-ac", "rt-ax", "dd-wrt", "openwrt", "pfsense",
                "opnsense", "edgerouter", "mikrotik", "gateway", "udm",
                "dream machine", "firewalla", "gl-inet", "gl-")),
    ("Laptop", ("macbook", "laptop", "thinkpad", "notebook", "elitebook",
                "probook", "latitude", "zenbook", "ideapad", "xps", "framework")),
    ("Desktop", ("desktop", "imac", "mac pro", "mac mini", "gigabyte", "asrock",
                 "optiplex", "workstation", " nuc", "odyssey", "msi ")),
    ("Phone", ("iphone", "galaxy s", "galaxy note", "pixel", "oneplus", "redmi",
               "xiaomi mi", "huawei", "smartphone", "phone")),
    ("Tablet", ("ipad", "tablet", "galaxy tab")),
    ("TV", ("smart tv", "lg tv", "samsung tv", "sony tv", "bravia", "android tv",
            "webos", "tizen", "vizio")),
    ("Multimedia", ("apple tv", "fire tv", "fire cube", "firestick", "fire stick",
                    "chromecast", "nest hub", "nest mini", "nest audio", "roku",
                    "shield", "homepod", "sonos", "echo", "alexa", "encoder",
                    "iptv", "hdmi", "google nest", "nvidia shield", "set-top",
                    "media", "soundbar")),
    ("Console", ("playstation", "ps4", "ps5", "xbox", "nintendo", "steam deck")),
    ("IoT Hub", ("aqara", "smartthings", "philips hue", "hue bridge", "zigbee",
                 "z-wave", "zwave", "hubitat", "homey", "deconz", "conbee",
                 "home assistant", "homeassistant")),
    ("Camera", ("camera", "reolink", "hikvision", "dahua", "nest cam", "wyze",
                "doorbell", "ring ", "protect", "webcam", "ipcam", "amcrest")),
    ("Printer", ("printer", "laserjet", "officejet", "epson", "brother",
                 "pixma", "deskjet", "ecotank")),
    ("NAS", ("synology", "qnap", "truenas", "diskstation", "unraid", "nas")),
    ("Server", ("server", "proxmox", "esxi", "vmware", "hypervisor", "raspberry",
                "raspberrypi")),
    ("Watch", ("apple watch", "watch", "fitbit", "garmin")),
)


def _client_category(text: str) -> str:
    """Best device-type label for a client from its name / hostname / vendor
    text (keyword match, most-specific-first), or '' when nothing matches."""
    t = " " + (text or "").strip().lower() + " "
    for label, kws in _CLIENT_CATEGORIES:
        for kw in kws:
            if kw in t:
                return label
    return ""


# Device-type EMOJI per category — the at-a-glance icon for each client row.
_DEVICE_EMOJI = {
    "Router": "🛜", "Laptop": "💻", "Desktop": "🖥️", "Phone": "📱",
    "Tablet": "📱", "TV": "📺", "Multimedia": "📺", "Console": "🎮",
    "IoT Hub": "🏠", "Camera": "📷", "Printer": "🖨️", "NAS": "🗄️",
    "Server": "🖥️", "Watch": "⌚",
}
# A leading "[Category] " token UniFi (or the operator) already put on the
# client NAME — stripped + turned into the device emoji so it isn't shown twice.
_LEADING_CATEGORY = re.compile(r"^\s*\[(?P<cat>[^]]+)]\s*")


def _client_display(name: str, text: str, wired: bool) -> "tuple[str, str]":
    """``(emoji, clean_name)`` for a client row: a device-type emoji + the name
    with any leading ``[Category]`` bracket removed (so the category shows as an
    ICON instead of duplicated text). The category is read from that existing
    bracket when it maps to a known type, else inferred from the name / vendor
    text; the emoji falls back to the wired/wireless glyph when unknown."""
    raw = (name or "").strip()
    cat = ""
    clean = raw
    m = _LEADING_CATEGORY.match(raw)
    if m:
        bracket = m.group("cat").strip().lower()
        for label in _DEVICE_EMOJI:
            if bracket == label.lower():
                cat = label
                clean = _LEADING_CATEGORY.sub("", raw).strip() or raw
                break
    if not cat:
        cat = _client_category(text or raw)
    emoji = _DEVICE_EMOJI.get(cat) or ("🔌" if wired else "📶")
    return emoji, clean


async def _fetch_client_usage(cli: "httpx.AsyncClient", base: str, key: str) -> list[dict]:
    """Per-client SESSION usage (rx + tx bytes since the client connected) from
    the LEGACY controller ``stat/sta`` endpoint — the Integration API client
    record carries no traffic counters, so the top-by-usage ranking needs the
    classic API (same ``X-API-KEY`` auth + per-site walk as ``_fetch_wlan_names``).
    Each row: ``{name, ip, wired, rx, tx, total, text}`` (bytes; ``text`` is the
    categorisation source). ``[]`` on any failure so the caller degrades to the
    plain count summary."""
    from urllib.parse import quote  # noqa: PLC0415
    out: list[dict] = []
    for sk in (await _legacy_site_keys(cli, base, key))[:_MAX_SITES]:
        body = await _get_json(
            cli, base + _LEGACY_API + f"/s/{quote(sk, safe='')}/stat/sta", key)
        for c in as_list(as_dict(body).get("data")):
            if not isinstance(c, dict):
                continue
            rx = safe_int(c.get("rx_bytes")) + safe_int(c.get("wired-rx_bytes"))
            tx = safe_int(c.get("tx_bytes")) + safe_int(c.get("wired-tx_bytes"))
            nm = str(c.get("name") or "").strip()
            hn = str(c.get("hostname") or "").strip()
            vendor = str(c.get("oui") or "").strip()
            name = nm or hn or str(c.get("mac") or "").strip()
            if not name:
                continue
            # Categorisation text — name + hostname + vendor OUI for the best
            # device-type keyword match (e.g. "MacBook Pro Laptop" → Laptop).
            cat_text = " ".join(x for x in (nm, hn, vendor) if x) or name
            out.append({"name": name, "ip": str(c.get("ip") or "").strip(),
                        "wired": bool(c.get("is_wired")), "rx": rx, "tx": tx,
                        "total": rx + tx, "text": cat_text})
    return out


# noinspection DuplicatedCode
async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch the UniFi fleet summary for the card. Lists sites, then fans out
    devices + clients per site (capped) and aggregates. Returns the card payload
    (see the module docstring). Raises ``ValueError`` / ``RuntimeError`` (caller
    maps to HTTPException) when the key is unset / the base URL won't resolve /
    the upstream errors."""
    key = (chip.get("api_key") or "").strip()
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=key, log_tag="unifi")
    if hit is not None:
        return hit
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            sites = await _list_sites(cli, base, key)
            if not sites:
                # Distinguish "auth/endpoint problem" from "genuinely no sites".
                probe = await _get_json(cli, base + _API + "/sites", key)
                if probe is None:
                    raise RuntimeError(
                        f"upstream returned no sites for {base}{_API}/sites "
                        f"(check the API key + that the Integration API is enabled "
                        f"on UniFi OS 4.x / Network 9.0+)")
            version = await _fetch_version(cli, base, key)
            per_site = await asyncio.gather(*[
                _gather_site(cli, base, key, str(s.get("id") or ""))
                for s in sites[:_MAX_SITES] if s.get("id")
            ])
            # The Integration API has no WLAN-config endpoint — read the SSID
            # list from the legacy controller API (same key). Cheap (1 + N-sites
            # calls) and the whole fetch is cached, so no per-poll cost concern.
            legacy_wlan_names = await _fetch_wlan_names(cli, base, key)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[unifi] error: fetch host={host_id} base={base} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")

    devices: list = []
    clients: list = []
    wlans: list = []
    for devs, clis, wls in per_site:
        devices.extend(devs)
        clients.extend(clis)
        wlans.extend(wls)

    online = sum(1 for d in devices
                 if str(d.get("state") or "").strip().lower() == "online")
    buckets = {"ap": 0, "switch": 0, "gateway": 0, "other": 0}
    for d in devices:
        buckets[_classify_device(d)] += 1
    wired = sum(1 for c in clients if _client_is_wired(c))
    updates = sum(1 for d in devices if _device_update_pending(d))
    # Configured Wi-Fi networks, most-authoritative-first:
    #   1) the Integration ``/wlans`` endpoint (if a future Network version adds
    #      it — accurate configured NAMES);
    #   2) the legacy controller ``rest/wlanconf`` (the real source today — the
    #      Integration API has no WLAN-config route, see _fetch_wlan_names);
    #   3) the distinct SSIDs seen across CURRENTLY-connected wireless clients
    #      (a heuristic — misses idle SSIDs and the Integration client record may
    #      not even carry the SSID, which is why 1+2 exist).
    # Names are sorted + deduped (case-insensitive) for the card / skill.
    if wlans:
        wlan_names = sorted({n for n in (_wlan_name(w) for w in wlans) if n},
                            key=str.lower)
        wlan_src = "integration"
    elif legacy_wlan_names:
        wlan_names = legacy_wlan_names
        wlan_src = "legacy"
    else:
        wlan_names = sorted({s for s in (_client_ssid(c) for c in clients) if s},
                            key=str.lower)
        wlan_src = "client-ssid"

    out: dict[str, Any] = {
        "available": True,
        "version": version,
        "sites": len(sites),
        "devices": len(devices),
        "devices_online": online,
        "devices_offline": max(0, len(devices) - online),
        "devices_update_available": updates,
        "aps": buckets["ap"],
        "switches": buckets["switch"],
        "gateways": buckets["gateway"],
        "clients": len(clients),
        "clients_wired": wired,
        "clients_wireless": max(0, len(clients) - wired),
        "wlans": len(wlan_names),
        "wlan_names": wlan_names[:_MAX_ROWS],
        "fetched_at": int(now),
    }
    print(f"[unifi] INFO fetched host={host_id} sites={out['sites']} "
          f"devices={out['devices']} online={out['devices_online']} "
          f"aps={out['aps']} sw={out['switches']} gw={out['gateways']} "
          f"clients={out['clients']} (w={out['clients_wired']}/"
          f"wl={out['clients_wireless']}) wlans={out['wlans']}({wlan_src}) "
          f"updates={out['devices_update_available']} ver={version or '-'}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "sites": safe_int(data.get("sites")),
        "devices": safe_int(data.get("devices")),
        "devices_online": safe_int(data.get("devices_online")),
        "devices_offline": safe_int(data.get("devices_offline")),
        "devices_update_available": safe_int(data.get("devices_update_available")),
        "aps": safe_int(data.get("aps")),
        "switches": safe_int(data.get("switches")),
        "gateways": safe_int(data.get("gateways")),
        "clients": safe_int(data.get("clients")),
        "clients_wired": safe_int(data.get("clients_wired")),
        "clients_wireless": safe_int(data.get("clients_wireless")),
        "wlans": safe_int(data.get("wlans")),
        "wlan_names": [str(n) for n in as_list(data.get("wlan_names")) if n],
        "version": data.get("version") or "",
        "fetched_at": safe_int(data.get("fetched_at")),
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
def _resolve_skill_target(host_row: dict, chip: dict) -> "tuple[str, str, Optional[dict]]":
    """Resolve ``(key, base)`` or a ready ``{ok: False, detail}`` error dict for
    a UniFi skill."""
    key = (chip.get("api_key") or "").strip()
    if not key:
        return "", "", {"ok": False, "status": 0, "detail": "UniFi API key not set"}
    base = resolve_base_url(host_row, chip)
    if not base:
        return "", "", {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    return key, base, None


def _guard(r: "httpx.Response") -> Optional[dict]:
    """Shared 401 / 403 + non-2xx guard. Returns a ready error dict, or None on
    a 2xx."""
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code,
                "detail": "auth failed (check the UniFi API key)"}
    if not (200 <= r.status_code < 300):
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    return None


async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None,
                    arg: Optional[str] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Returns ``{ok, detail, status?}``.
    Raises ValueError on an unknown skill id (route maps to HTTP 404)."""
    if skill_id == "unifi_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "unifi_devices":
        return await _devices_skill(host_row, chip, host_id=host_id)
    if skill_id == "unifi_clients":
        return await _clients_skill(host_row, chip, host_id=host_id,
                                    service_idx=service_idx)
    if skill_id == "unifi_wlans":
        return await _wlans_skill(host_row, chip, host_id=host_id,
                                  service_idx=service_idx)
    if skill_id == "unifi_restart_device":
        return await _restart_device_skill(host_row, chip, arg=arg, host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


def _attach_items(out: dict, items: list, count_i18n: str) -> dict:
    """Attach the rich-item list + count + count-i18n key to a skill result
    (no-op when empty). Returns ``out`` for one-line use."""
    if items:
        out["items"] = items
        out["count"] = len(items)
        out["count_i18n"] = count_i18n
    return out


async def _live_data(host_row: dict, chip: dict, host_id: Optional[str],
                     service_idx: Optional[int]) -> "tuple[Optional[dict], Optional[dict]]":
    """Force-fetch the card payload for a skill (cache-bypassing). Returns
    ``(data, None)`` on success or ``(None, error_dict)`` when ``fetch_data``
    raised — folds the identical fetch + except preamble the status / clients
    skills share."""
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        return None, {"ok": False, "detail": str(e), "status": 0}
    return data, None


async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch the fleet summary (force-bypasses the cache) and
    return a formatted ``detail``. Never raises."""
    print(f"[unifi] INFO unifi_status host={host_id} svc_idx={service_idx} (live fetch)")
    data, err = await _live_data(host_row, chip, host_id, service_idx)
    if data is None:
        return err or {"ok": False, "detail": "fetch failed", "status": 0}
    dev = safe_int(data.get("devices"))
    online = safe_int(data.get("devices_online"))
    offline = safe_int(data.get("devices_offline"))
    updates = safe_int(data.get("devices_update_available"))
    lines = [
        f"📡 Devices: {online}/{dev} online" + (f" · {offline} offline" if offline else ""),
        f"🛜 {safe_int(data.get('aps'))} APs · 🔀 {safe_int(data.get('switches'))} "
        f"switches · 🛡️ {safe_int(data.get('gateways'))} gateways",
        f"👥 Clients: {safe_int(data.get('clients'))} "
        f"({safe_int(data.get('clients_wired'))} wired · "
        f"{safe_int(data.get('clients_wireless'))} wireless)",
    ]
    wlans = safe_int(data.get("wlans"))
    if wlans:
        lines.append(f"📶 {wlans} Wi-Fi network(s)")
    if updates:
        lines.append(f"⬆️ {updates} device firmware update(s) available")
    ver = str(data.get("version") or "").strip()
    sites = safe_int(data.get("sites"))
    tail = []
    if sites:
        tail.append(f"{sites} site(s)")
    if ver:
        tail.append(f"Network {ver}")
    if tail:
        lines.append("· " + " · ".join(tail))
    return {"ok": True, "detail": "\n".join(lines), "status": 200,
            "devices": dev, "online": online, "offline": offline}


def _device_row(d: dict) -> Optional[dict]:
    """One device as a rich skill-result item: name + a state / model / IP
    subtitle, grouped by device type. Carries an internal ``_bucket`` for
    grouping (stripped before the item ships)."""
    name = str(d.get("name") or "").strip() or str(d.get("model") or "").strip()
    if not name:
        return None
    state = str(d.get("state") or "").strip().lower()
    emoji = _STATE_EMOJI.get(state, "⚪")
    bits = [f"{emoji} {state.replace('_', ' ') or 'unknown'}"]
    model = str(d.get("model") or "").strip()
    if model:
        bits.append(model)
    ip = str(d.get("ipAddress") or "").strip()
    if ip:
        bits.append(ip)
    return {"title": name, "subtitle": " · ".join(bits), "_bucket": _classify_device(d)}


# Device-type group order + i18n header key for the rich device list.
_BUCKET_ORDER = {"gateway": 0, "switch": 1, "ap": 2, "other": 3}
_BUCKET_I18N = {
    "gateway": "apps.unifi.group_gateways", "switch": "apps.unifi.group_switches",
    "ap": "apps.unifi.group_aps", "other": "apps.unifi.group_other",
}


async def _devices_skill(host_row: dict, chip: dict, *,
                         host_id: Optional[str] = None) -> dict:
    """Read-only: list adopted devices as rich rows grouped by type (gateway /
    switch / AP / other) with a state dot. Fetches devices directly (no
    ``service_idx`` — it doesn't read the per-card cache). Never raises."""
    key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    print(f"[unifi] INFO unifi_devices host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            sites = await _list_sites(cli, base, key)
            devs_nested = await asyncio.gather(*[
                _get_all(cli, base + _API + f"/sites/{s.get('id')}/devices", key)
                for s in sites[:_MAX_SITES] if s.get("id")
            ])
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"fetch failed: {type(e).__name__}: {e}"}
    devices: list = []
    for body in devs_nested:
        devices.extend(body)
    if not devices:
        return {"ok": True, "status": 200, "detail": "📡 No UniFi devices found."}
    rows = [r for r in (_device_row(d) for d in devices) if r is not None]
    rows.sort(key=lambda r: (_BUCKET_ORDER.get(r["_bucket"], 9), r["title"].lower()))
    mixed = len({r["_bucket"] for r in rows}) > 1
    items: list = []
    lines: list = []
    for r in rows[:_MAX_ROWS]:
        it: dict = {"title": r["title"], "subtitle": r["subtitle"]}
        if mixed:
            it["group"] = _BUCKET_I18N.get(r["_bucket"], "apps.unifi.group_other")
        items.append(it)
        lines.append(f"• {r['title']}  ({r['subtitle']})")
    out: dict = {"ok": True, "status": 200,
                 "detail": "📡 UniFi devices:\n" + "\n".join(lines)}
    return _attach_items(out, items, "apps.unifi.devices_count")


async def _clients_skill(host_row: dict, chip: dict, *,
                         host_id: Optional[str] = None,
                         service_idx: Optional[int] = None) -> dict:
    """Read-only: connected-client counts (total / wired / wireless) PLUS a
    top-by-usage rich list — the busiest clients this session, ranked by
    rx + tx bytes (from the legacy ``stat/sta`` endpoint). The rich list is
    omitted (only the count summary returns) when that endpoint is unavailable
    or every client shows zero traffic. Never raises."""
    print(f"[unifi] INFO unifi_clients host={host_id} (live fetch)")
    data, err = await _live_data(host_row, chip, host_id, service_idx)
    if data is None:
        return err or {"ok": False, "detail": "fetch failed", "status": 0}
    total = safe_int(data.get("clients"))
    wired = safe_int(data.get("clients_wired"))
    wireless = safe_int(data.get("clients_wireless"))
    summary = (f"👥 {total} client(s) connected — {wired} wired · "
               f"{wireless} wireless.")
    base_out = {"ok": True, "status": 200, "detail": summary,
                "clients": total, "wired": wired, "wireless": wireless}
    # Per-client usage for the top-N ranking — legacy controller stat/sta (the
    # Integration API client record has no traffic counters). Best-effort: any
    # failure (legacy API off, 401) falls back to the plain count summary.
    key, base, terr = _resolve_skill_target(host_row, chip)
    rows: list[dict] = []
    if terr is None:
        try:
            async with httpx.AsyncClient(verify=False, timeout=20.0,
                                         follow_redirects=True) as cli:
                rows = await _fetch_client_usage(cli, base, key)
        except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
            print(f"[unifi] warning: client-usage fetch failed for {base} — "
                  f"{type(e).__name__}: {e}")
            rows = []
    rows = [r for r in rows if r["total"] > 0]
    if not rows:
        return base_out
    rows.sort(key=lambda r: r["total"], reverse=True)
    top = rows[:10]
    maxt = top[0]["total"] or 1
    items: list[dict] = []
    lines = [summary, "", "📊 Top clients by usage (this session):"]
    for i, c in enumerate(top, 1):
        emoji, clean_name = _client_display(c["name"], c.get("text") or "",
                                            bool(c["wired"]))
        conn = "🔌" if c["wired"] else "📶"
        sub_bits = [conn]
        if c["ip"]:
            sub_bits.append(c["ip"])
        sub_bits.append(f"▼ {_fmt_bytes(c['rx'])} · ▲ {_fmt_bytes(c['tx'])}")
        items.append({
            "title": f"{emoji} {clean_name}",
            "subtitle": " · ".join(sub_bits),
            "progress": round(c["total"] / maxt * 100),
        })
        lines.append(f"{i}. {emoji} {clean_name} — {_fmt_bytes(c['total'])} "
                     f"(▼{_fmt_bytes(c['rx'])} ▲{_fmt_bytes(c['tx'])})")
    out = dict(base_out)
    out["detail"] = "\n".join(lines)
    return _attach_items(out, items, "apps.unifi.clients_top_count")


async def _wlans_skill(host_row: dict, chip: dict, *,
                       host_id: Optional[str] = None,
                       service_idx: Optional[int] = None) -> dict:
    """Read-only: list the configured Wi-Fi networks (SSIDs) as rich rows. The
    names come from the WLANs endpoint when available, else the distinct SSIDs
    seen across wireless clients. Never raises."""
    print(f"[unifi] INFO unifi_wlans host={host_id} (live fetch)")
    data, err = await _live_data(host_row, chip, host_id, service_idx)
    if data is None:
        return err or {"ok": False, "detail": "fetch failed", "status": 0}
    names = [str(n) for n in as_list(data.get("wlan_names")) if n]
    if not names:
        return {"ok": True, "status": 200, "detail": "📶 No Wi-Fi networks found."}
    items = [{"title": n, "subtitle": "SSID"} for n in names]
    lines = [f"• {n}" for n in names]
    out: dict = {"ok": True, "status": 200,
                 "detail": f"📶 {len(names)} Wi-Fi network(s):\n" + "\n".join(lines),
                 "wlans": len(names)}
    return _attach_items(out, items, "apps.unifi.wlans_count")


async def _restart_device_skill(host_row: dict, chip: dict, *,
                                arg: Optional[str],
                                host_id: Optional[str] = None) -> dict:
    """DESTRUCTIVE: restart ONE device by name or MAC. Resolves the device
    across the console's sites, then POSTs the ``RESTART`` action. Never
    raises."""
    needle = (arg or "").strip()
    if not needle:
        return {"ok": False, "status": 0,
                "detail": "no device name given (say e.g. \"restart the Garage AP\")"}
    key, base, err = _resolve_skill_target(host_row, chip)
    if err:
        return err
    needle_l = needle.lower()
    needle_mac = needle_l.replace("-", ":")
    print(f"[unifi] INFO unifi_restart_device host={host_id} target={needle!r}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            sites = await _list_sites(cli, base, key)
            match_site = ""
            match_dev = ""
            match_name = ""
            for s in sites[:_MAX_SITES]:
                sid = str(s.get("id") or "")
                if not sid:
                    continue
                for d in await _get_all(cli, base + _API + f"/sites/{sid}/devices", key):
                    dname = str(d.get("name") or "").strip()
                    dmac = str(d.get("macAddress") or "").strip().lower().replace("-", ":")
                    if dname.lower() == needle_l or dmac == needle_mac:
                        match_site, match_dev, match_name = sid, str(d.get("id") or ""), dname
                        break
                    if not match_dev and needle_l in dname.lower():
                        match_site, match_dev, match_name = sid, str(d.get("id") or ""), dname
                if match_dev and (match_name.lower() == needle_l):
                    break
            if not match_dev:
                return {"ok": False, "status": 404,
                        "detail": f"no UniFi device matched \"{needle}\""}
            ar = await cli.post(
                base + _API + f"/sites/{match_site}/devices/{match_dev}/actions",
                headers=_headers(key), json={"action": "RESTART"})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"restart failed: {type(e).__name__}: {e}"}
    guard = _guard(ar)
    if guard:
        return guard
    return {"ok": True, "status": 200,
            "detail": f"🔁 Restart triggered on \"{match_name}\". It will drop off "
                      f"the network for a minute while it reboots."}
