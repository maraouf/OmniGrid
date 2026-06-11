"""qBittorrent per-app module.

Single-instance per-app integration (one card per pinned chip — NOT a fleet
app). The operator can pin SEVERAL qBittorrent instances (e.g. a seedbox + a
local box); each renders its OWN card, and the AI / Telegram target a SPECIFIC
instance the same way every single-instance app does — this chip's
``available_app_skills_context()`` entry carries its ``host_id`` / ``host`` /
``service_idx`` so the model can pick one, and the Telegram dispatcher prompts
"runs on multiple hosts: …, specify one" when two are pinned and none is named.
To make two instances easy to tell apart, give each chip a distinct NAME
override in Admin → Apps (it flows into the AI context's ``app`` field, so the
operator can say "pause all on the seedbox qbittorrent").

Auth model — WebUI API v2 session cookie (username + password, NOT a static
header):
  1. ``POST /api/v2/auth/login`` (form-encoded ``username`` + ``password``,
     with a ``Referer: <base>`` header qBittorrent's CSRF check requires) → on
     success the response sets an ``SID`` cookie (the body is the literal
     "Ok.").
  2. Every subsequent call reuses that cookie (the SAME httpx client / cookie
     jar). We re-authenticate per fetch (one cheap extra round-trip) rather than
     caching the SID across the process — stateless + correct on a credential
     rotation, same rationale as Kavita's JWT. The PASSWORD is the secret
     (``api_key``, keep-current-if-blank); the USERNAME is the plain second
     field (like AdGuard). EACH instance has its OWN login — credentials are
     per-chip (the per-instance editor saves this chip's ``username`` +
     ``api_key`` independently), so two pinned qBittorrents each need their own.
     ``fetch_data`` requires the password (a chip without one isn't configured
     yet — the card says so, per-instance, instead of attempting the API and
     surfacing a confusing 403).

The expanded card answers "what's this client doing right now" at a glance:
    dl_speed / up_speed   — live transfer rates       (GET /api/v2/transfer/info)
    torrents_total        — torrents in the client     (GET /api/v2/torrents/info)
    downloading / seeding / paused / completed — counts by state
    version               — qBittorrent version        (GET /api/v2/app/version)

AI / Telegram skills (per-instance — the AI / Telegram pick which qBittorrent):
* ``qbittorrent_status``     — read-only transfer + torrent-count summary.
* ``qbittorrent_list``       — read-only LISTING of torrents (name + state +
                               progress); optional ``arg`` filters by state
                               (downloading / seeding / paused / completed /
                               all).
* ``qbittorrent_add``        — add a torrent by magnet link or .torrent URL
                               (``arg``).
* ``qbittorrent_resume_all`` — resume (start) every torrent.
* ``qbittorrent_pause_all``  — pause (stop) every torrent (destructive-confirm).

Upstream API reference: <qbit-host>/api/v2 (WebUI API). Endpoints:
    POST /api/v2/auth/login            — session login (SID cookie)
    GET  /api/v2/app/version           — version (footnote)
    GET  /api/v2/transfer/info         — global dl/up speeds + totals
    GET  /api/v2/torrents/info         — torrent list (state / progress / speeds)
    POST /api/v2/torrents/add          — add by urls=<magnet|url>
    POST /api/v2/torrents/pause|stop   — pause all (hashes=all; 4.x pause / 5.x stop)
    POST /api/v2/torrents/resume|start — resume all (hashes=all; 4.x resume / 5.x start)
"""
from __future__ import annotations

import json as _json
import posixpath as _posixpath
import re as _re
import time
from typing import Any, Optional

import httpx

from logic.apps._common import resolve_base_url, resolve_cache_ttl
from logic.external_urls import ExternalURL

# VueTorrent WebUI auto-update (the alternative qBittorrent WebUI). The check
# skill compares the running version (served at /version.txt, or parsed from the
# alternative_webui_path) against the latest GitHub release; the update skill
# SSHes to the host, downloads + extracts the new release next to the current
# one, and points qBittorrent's alternative WebUI at it via the API.
_VUETORRENT_REPO = "VueTorrent/VueTorrent"
_SEMVER_RE = _re.compile(r"(?P<v>\d+\.\d+\.\d+)")
# Strict guards for the values interpolated into the SSH command (defence
# against shell injection — the version comes from a GitHub tag, the dir from
# qBittorrent's own prefs, but both are validated before they reach the shell).
_STRICT_SEMVER_RE = _re.compile(r"^\d+\.\d+\.\d+$")
_SAFE_PATH_RE = _re.compile(r"^/[A-Za-z0-9_./ -]+$")
from logic.apps._common import cache_key, peek_cache, resolve_userpass
from logic.coerce import as_list, safe_float, safe_int

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("qbittorrent",)

SKILLS: tuple[dict, ...] = (
    {
        "id": "qbittorrent_status",
        "name": "qBittorrent status",
        "ai_phrases": ("qbittorrent status, torrent status, what's downloading, "
                       "download speed, upload speed, how many torrents, "
                       "torrent client status, qbit status, seeding status, "
                       "how fast am i downloading"),
        "destructive": False,
    },
    {
        "id": "qbittorrent_downloading",
        "name": "What's downloading",
        "ai_phrases": ("what's downloading on qbittorrent, what is downloading "
                       "right now, show active downloads, download progress, "
                       "torrents in progress, what's being downloaded, current "
                       "downloads and progress, how far along are my downloads, "
                       "show me what qbittorrent is downloading"),
        "destructive": False,
    },
    {
        "id": "qbittorrent_list",
        "name": "List torrents",
        "ai_phrases": ("list my torrents, what torrents are downloading, show "
                       "torrents, what's seeding, list paused torrents, which "
                       "torrents are stalled, name the torrents, what's in "
                       "qbittorrent, list completed torrents, what am i "
                       "downloading right now"),
        "destructive": False,
        "arg": True,
        "arg_hint": ("OPTIONAL state filter: 'downloading' / 'seeding' / "
                     "'paused' / 'completed' / 'all'. Leave blank for the "
                     "active (downloading + seeding) torrents"),
    },
    {
        "id": "qbittorrent_add",
        "name": "Add a torrent",
        "ai_phrases": ("add a torrent, add this magnet, download this torrent, "
                       "add magnet <link>, grab this torrent, add torrent url "
                       "<link>, start downloading <link>"),
        "destructive": False,
        "arg": True,
        "arg_hint": "a magnet: link or an http(s) URL to a .torrent file",
    },
    {
        "id": "qbittorrent_delete",
        "name": "Delete a torrent",
        "ai_phrases": ("delete a torrent, remove a torrent, cancel a download, "
                       "delete this download, remove torrent <hash>, cancel "
                       "torrent, stop and delete a torrent"),
        "destructive": True,
        "arg": True,
        "arg_hint": ("the torrent HASH to delete (also removes its downloaded "
                     "files); the drawer's per-row trash button supplies it"),
    },
    {
        "id": "qbittorrent_resume_all",
        "name": "Resume all torrents",
        "ai_phrases": ("resume all torrents, start all torrents, unpause "
                       "everything, resume downloads, start downloading again, "
                       "un-pause all torrents"),
        "destructive": False,
    },
    {
        "id": "qbittorrent_pause_all",
        "name": "Pause all torrents",
        "ai_phrases": ("pause all torrents, stop all torrents, pause everything, "
                       "stop downloading, halt all torrents, pause downloads"),
        "destructive": True,
    },
    {
        "id": "qbittorrent_vuetorrent_check",
        "name": "Check VueTorrent version",
        "ai_phrases": ("what vuetorrent version, is vuetorrent up to date, check "
                       "the vuetorrent webui version, is my qbittorrent webui "
                       "current, vuetorrent update available, check for a "
                       "vuetorrent update, what's the latest vuetorrent"),
        "destructive": False,
    },
    {
        "id": "qbittorrent_vuetorrent_update",
        "name": "Update VueTorrent WebUI",
        "ai_phrases": ("update vuetorrent, upgrade the qbittorrent webui, install "
                       "the latest vuetorrent, update my qbittorrent webui to the "
                       "latest, bring vuetorrent up to date"),
        # Destructive: it SSHes to the host, downloads + extracts a new WebUI
        # release, and repoints qBittorrent's alternative WebUI at it.
        "destructive": True,
    },
)

# Per-(host_id, service_idx) data cache for the expanded card. 15s default —
# transfer speeds move fast, but a dashboard card doesn't need sub-15s freshness
# and this caps logins to one per ~15s on the hot path. Operator-overridable per
# chip via the editor's cache_ttl field.
DEFAULT_CACHE_TTL_S = 15
_data_cache: dict[str, tuple[float, dict]] = {}


def requires_api_key() -> bool:
    """qBittorrent's WebUI authenticates with a username + password; the
    PASSWORD is the secret (api_key) the editor + route gate on. The editor
    renders the username input + password input + Test-connection button."""
    return True



async def _login(cli: httpx.AsyncClient, base: str, username: str, password: str) -> None:
    """``POST /api/v2/auth/login`` — on success the client's cookie jar holds the
    SID cookie (auto-sent on subsequent calls). No-op when BOTH username +
    password are blank (auth-bypass WebUI). Raises ``RuntimeError`` on failure."""
    if not username and not password:
        return  # bypass-auth WebUI — try the API directly
    url = base + "/api/v2/auth/login"
    try:
        # qBittorrent's CSRF guard requires the Referer / Origin to match the
        # WebUI host, else the login is rejected with HTTP 403.
        r = await cli.post(url, data={"username": username, "password": password},
                           headers={"Referer": base, "Origin": base})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        raise RuntimeError(f"auth request failed: {type(e).__name__}: {e}")
    if r.status_code == 403:
        raise RuntimeError("auth failed: HTTP 403 (qBittorrent rejected the login — "
                           "check the WebUI host/Referer, or the IP isn't temporarily banned)")
    if r.status_code != 200:
        raise RuntimeError(f"auth returned HTTP {r.status_code}")
    if (r.text or "").strip().lower().startswith("fail"):
        raise RuntimeError("auth failed (check username / password)")


async def _api_version(cli: httpx.AsyncClient, base: str) -> str:
    """Best-effort qBittorrent version via ``GET /api/v2/app/version`` on an
    already-authenticated client. ``""`` on any failure (never load-bearing)."""
    try:
        r = await cli.get(base + "/api/v2/app/version")
        if r.status_code != 200:
            return ""
        return (r.text or "").strip().strip('"')
    except (httpx.HTTPError, OSError):
        return ""


# Torrent ``state`` → coarse bucket for the card counts + the list skill.
# (Completed-ness is computed separately from ``progress`` by the callers.)
def _classify(state: Any) -> str:
    """Bucket a qBittorrent torrent state into downloading / seeding / paused /
    error / other. Covers 4.x (paused*) AND 5.x (stopped*) names + the
    stalled / queued / forced / checking variants."""
    s = str(state or "").strip().lower()
    if "paused" in s or "stopped" in s:
        return "paused"
    if s in ("error", "missingfiles"):
        return "error"
    if s.endswith("up") or s == "uploading":  # uploading / stalledUP / queuedUP / forcedUP / checkingUP
        return "seeding"
    if s.endswith("dl") or s in ("downloading", "metadl", "allocating"):
        return "downloading"
    return "other"


def _fmt_speed(bytes_per_s: Any) -> str:
    """Render a bytes/second rate as a human transfer speed (B/s … TiB/s).
    ``"0 B/s"`` for zero / missing."""
    v = safe_float(bytes_per_s)
    if v <= 0:
        return "0 B/s"
    units = ("B/s", "KiB/s", "MiB/s", "GiB/s", "TiB/s")
    idx = 0
    while v >= 1024 and idx < len(units) - 1:
        v /= 1024
        idx += 1
    return f"{v:,.1f} {units[idx]}"


async def test_credential(host_row: dict, chip: dict, candidate_key: str, *,
                          payload: Optional[dict] = None, **_kw) -> dict:
    """Probe qBittorrent by logging in (when creds are set) + reading the
    version. ``candidate_key`` is the password; the username comes from the test
    payload (pre-save) or the stored chip. Returns ``{ok, detail, status}``.
    Falls back to the chip's stored password when ``candidate_key`` is blank so a
    re-test after first save doesn't need a retype."""
    pay = payload or {}
    username, password = resolve_userpass(
        chip,
        password=(candidate_key or "").strip() or None,
        username=(pay.get("username") or "").strip() or None,
    )
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "detail": "no upstream URL configured", "status": 0}
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            await _login(cli, base, username, password)
            ver = await _api_version(cli, base)
            # version is unauthenticated-friendly only AFTER login; a 403 here
            # means the cookie wasn't accepted (bad creds / not bypassed).
            r = await cli.get(base + "/api/v2/transfer/info")
    except RuntimeError as e:
        return {"ok": False, "detail": str(e), "status": 0}
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if r.status_code == 200:
        return {"ok": True, "detail": f"OK (qBittorrent {ver})" if ver else "OK",
                "status": 200}
    if r.status_code in (401, 403):
        return {"ok": False, "detail": "auth failed (check username / password)",
                "status": r.status_code}
    return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}


# noinspection DuplicatedCode
# The httpx-client + login + GET + upstream-error-guard shape below is
# structurally shared with this module's skills AND every sibling per-app
# module's fetch_data — the deliberate per-app encapsulation pattern (CLAUDE.md).
# The content (qBittorrent session auth, endpoints, fields) differs, so it stays
# inline rather than coupling modules through a shared helper.
async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch qBittorrent's transfer + torrent summary for the expanded card.

    Returns ``{available, dl_speed, up_speed, dl_total, up_total,
    torrents_total, downloading, seeding, paused, completed, version,
    fetched_at}``. Raises ``ValueError`` / ``RuntimeError`` when the base URL
    won't resolve / auth fails / the upstream errors."""
    username, password = resolve_userpass(chip)
    base = resolve_base_url(host_row, chip)
    if not base:
        raise ValueError("no upstream URL configured")
    # Each qBittorrent instance has its OWN login — a chip with no password
    # isn't configured yet. Fail early with an actionable, per-instance message
    # (instead of attempting the API and surfacing a confusing 403) so the card
    # tells the operator to set THIS instance's credentials.
    if not password:
        raise ValueError("no password set for this qBittorrent instance — add a "
                         "username + password in Admin → Apps (each instance has "
                         "its own login)")
    now = time.time()
    ttl = resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S)
    ck = cache_key(host_id, service_idx)
    cached = _data_cache.get(ck)
    if cached and not force and (now - cached[0]) < ttl:
        return cached[1]
    info_url = base + "/api/v2/transfer/info"
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            await _login(cli, base, username, password)
            tr = await cli.get(info_url)
            tor = await cli.get(base + "/api/v2/torrents/info")
            ver = await _api_version(cli, base)
    except RuntimeError as e:
        print(f"[qbittorrent] error: fetch host={host_id} — {e}")
        raise RuntimeError(str(e))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[qbittorrent] error: fetch host={host_id} url={info_url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    if tr.status_code in (401, 403):
        print(f"[qbittorrent] error: fetch host={host_id} url={info_url} returned "
              f"HTTP {tr.status_code} (auth — check this instance's username / password)")
        raise RuntimeError(f"auth failed: HTTP {tr.status_code} — check THIS instance's "
                           f"qBittorrent username / password (each instance has its own login)")
    if tr.status_code != 200:
        print(f"[qbittorrent] error: fetch host={host_id} url={info_url} returned "
              f"HTTP {tr.status_code} (check the chip URL points at the qBittorrent "
              f"WebUI root, e.g. https://qbit.example.com)")
        raise RuntimeError(f"upstream returned HTTP {tr.status_code} for {info_url}")
    try:
        info = tr.json()
    except (ValueError, TypeError):  # noqa: BLE001
        raise RuntimeError("upstream returned non-JSON")
    if not isinstance(info, dict):
        info = {}
    # Guard the torrents-list parse the same way as `info` above — a 200 with a
    # non-JSON body (reverse-proxy error page on this route) would otherwise
    # raise a raw ValueError instead of the RuntimeError the docstring promises.
    try:
        torrents = as_list(tor.json()) if tor.status_code == 200 else []
    except (ValueError, TypeError):  # noqa: BLE001
        torrents = []
    downloading = seeding = paused = completed = 0
    for t in torrents:
        if not isinstance(t, dict):
            continue
        bucket = _classify(t.get("state"))
        if bucket == "downloading":
            downloading += 1
        elif bucket == "seeding":
            seeding += 1
        elif bucket == "paused":
            paused += 1
        if safe_float(t.get("progress")) >= 1.0:
            completed += 1
    out: dict[str, Any] = {
        "available": True,
        "dl_speed": safe_int(info.get("dl_info_speed")),
        "up_speed": safe_int(info.get("up_info_speed")),
        "dl_total": safe_int(info.get("dl_info_data")),
        "up_total": safe_int(info.get("up_info_data")),
        "torrents_total": len(torrents),
        "downloading": downloading,
        "seeding": seeding,
        "paused": paused,
        "completed": completed,
        "version": ver,
        "fetched_at": int(now),
    }
    print(f"[qbittorrent] INFO fetched host={host_id} torrents={out['torrents_total']} "
          f"dl={out['downloading']} seed={out['seeding']} paused={out['paused']} "
          f"dl_speed={out['dl_speed']} up_speed={out['up_speed']}")
    _data_cache[ck] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "dl_speed": safe_int(data.get("dl_speed")),
        "up_speed": safe_int(data.get("up_speed")),
        "torrents_total": safe_int(data.get("torrents_total")),
        "downloading": safe_int(data.get("downloading")),
        "seeding": safe_int(data.get("seeding")),
        "paused": safe_int(data.get("paused")),
        "completed": safe_int(data.get("completed")),
        "version": data.get("version") or "",
        "fetched_at": safe_int(data.get("fetched_at")),
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None,
                    arg: Optional[str] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Raises ValueError on an unknown skill
    id. ``arg`` carries the free-form argument (a state filter for the list
    skill, or a magnet / URL for the add skill). The route also passes
    ``actor_username`` — absorbed by ``**_kw`` since no qBittorrent skill needs
    the acting user."""
    if skill_id == "qbittorrent_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "qbittorrent_downloading":
        return await _downloading_skill(host_row, chip, host_id=host_id)
    if skill_id == "qbittorrent_list":
        return await _list_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "qbittorrent_add":
        return await _add_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "qbittorrent_delete":
        return await _delete_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "qbittorrent_resume_all":
        return await _set_all_skill(host_row, chip, resume=True, host_id=host_id)
    if skill_id == "qbittorrent_pause_all":
        return await _set_all_skill(host_row, chip, resume=False, host_id=host_id)
    if skill_id == "qbittorrent_vuetorrent_check":
        return await _vuetorrent_check_skill(host_row, chip, host_id=host_id)
    if skill_id == "qbittorrent_vuetorrent_update":
        return await _vuetorrent_update_skill(host_row, chip, host_id=host_id,
                                              actor_username=_kw.get("actor_username"))
    raise ValueError(f"unknown skill: {skill_id!r}")


def _resolve_target(host_row: dict, chip: dict) -> "tuple[str, str, str, Optional[dict]]":
    """Resolve ``(username, password, base)`` or return a ready
    ``{ok: False, detail}`` when the base URL won't resolve."""
    username, password = resolve_userpass(chip)
    base = resolve_base_url(host_row, chip)
    if not base:
        return "", "", "", {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    return username, password, base, None


# ---------------------------------------------------------------------------
# VueTorrent WebUI version check + auto-update
# ---------------------------------------------------------------------------
def _semver_tuple(s: Any) -> tuple:
    """``"2.34.0"`` → ``(2, 34, 0)`` for ordering. Missing / bad parts → 0."""
    out: list = []
    for part in str(s or "").split(".")[:3]:
        try:
            out.append(int(part))
        except (TypeError, ValueError):
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


async def _qbit_prefs(cli: httpx.AsyncClient, base: str) -> dict:
    """``GET /api/v2/app/preferences`` (authenticated). ``{}`` on any failure."""
    try:
        r = await cli.get(base + "/api/v2/app/preferences")
        if r.status_code == 200:
            body = r.json()
            return body if isinstance(body, dict) else {}
    except (httpx.HTTPError, OSError, ValueError, TypeError):
        return {}
    return {}


async def _current_vuetorrent(cli: httpx.AsyncClient, base: str,
                              prefs: dict) -> "tuple[str, str]":
    """Detect the RUNNING VueTorrent version + its WebUI root folder.

    Returns ``(version, root_folder)``. Version comes from ``/version.txt`` (the
    VueTorrent build ships it at its dist root, served by qBittorrent from the
    alternative-WebUI root); falls back to parsing the version out of the
    ``alternative_webui_path`` dir name (e.g. ``…/VueTorrent.2.34.0``)."""
    ver = ""
    try:
        r = await cli.get(base + "/version.txt")
        if r.status_code == 200:
            m = _SEMVER_RE.search((r.text or "").strip())
            if m:
                ver = m.group("v")
    except (httpx.HTTPError, OSError):
        ver = ""
    root = str((prefs or {}).get("alternative_webui_path") or "").strip()
    if not ver and root:
        m = _SEMVER_RE.search(root)
        if m:
            ver = m.group("v")
    return ver, root


async def _latest_vuetorrent() -> "tuple[str, str, str]":
    """Latest VueTorrent release from GitHub. Returns ``(version, zip_url,
    error)`` — ``error`` is non-empty on failure (rate limit / network)."""
    url = f"{ExternalURL.GITHUB_API}/repos/{_VUETORRENT_REPO}/releases/latest"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "OmniGrid"}
    from logic.env_keys import EnvKey, env_get  # noqa: PLC0415
    tok = (env_get(EnvKey.GITHUB_TOKEN) or "").strip()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as cli:
            r = await cli.get(url, headers=headers)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return "", "", f"GitHub fetch failed: {type(e).__name__}: {e}"
    if r.status_code != 200:
        return "", "", f"GitHub returned HTTP {r.status_code}"
    try:
        body = r.json()
    except (ValueError, TypeError):
        return "", "", "GitHub returned non-JSON"
    tag = str((body or {}).get("tag_name") or "").strip()
    m = _SEMVER_RE.search(tag)
    if not m:
        return "", "", "could not parse the latest release tag"
    ver = m.group("v")
    zip_url = ""
    assets = body.get("assets") if isinstance(body, dict) else None
    for a in (assets if isinstance(assets, list) else []):
        if isinstance(a, dict) and str(a.get("name") or "").lower() == "vuetorrent.zip":
            zip_url = str(a.get("browser_download_url") or "").strip()
            break
    if not zip_url:
        zip_url = (f"{ExternalURL.GITHUB}/{_VUETORRENT_REPO}/releases/download/"
                   f"{tag}/vuetorrent.zip")
    return ver, zip_url, ""


def _vuetorrent_install_script(install_dir: str, version: str, zip_url: str) -> str:
    """Build the one SSH command that downloads + extracts the VueTorrent
    release next to the current install. Every interpolated value is strict-
    regex-validated by the caller (no quotes / shell metacharacters), and the
    values are single-quoted, so this is injection-safe. curl→wget and
    unzip→python3 fallbacks cover minimal hosts. Echoes ``INSTALLED:<dir>`` on
    success so the caller can confirm."""
    z = f"VueTorrent.{version}.zip"
    dest = f"VueTorrent.{version}"
    return (
        "set -e; "
        f"DIR='{install_dir}'; "
        f"URL='{zip_url}'; ZIP='{z}'; DEST='{dest}'; "
        'test -d "$DIR" || { echo "ERROR: install dir $DIR does not exist"; exit 1; }; '
        # The install dir is usually owned by the qBittorrent SERVICE user (e.g.
        # qbittorrent-nox), not the SSH user — so write directly when possible,
        # else escalate the FINAL move into $DIR via passwordless sudo. Download
        # + extract happen in a writable temp dir first, so they never need
        # privilege; only the move-into-place + chown do.
        'if [ -w "$DIR" ]; then SUDO=""; '
        'elif sudo -n true 2>/dev/null; then SUDO="sudo -n"; '
        'else echo "ERROR: $DIR is not writable by $(whoami) and passwordless sudo is unavailable '
        '— grant write access (chown it to the SSH user) OR enable NOPASSWD sudo for the SSH user, then retry"; exit 1; fi; '
        'OWNER=$(stat -c \'%U:%G\' "$DIR" 2>/dev/null || echo ""); '
        'TMP=$(mktemp -d); trap \'rm -rf "$TMP"\' EXIT; '
        'AVAIL=$(df -Pk "$DIR" 2>/dev/null | awk \'NR==2{print $4}\'); echo "FREE_KB:${AVAIL:-?}"; '
        'if command -v curl >/dev/null 2>&1; then '
        'curl -fSL -o "$TMP/$ZIP" "$URL" || { echo "ERROR: download failed (curl) — check connectivity / disk space"; exit 1; }; '
        'else wget -O "$TMP/$ZIP" "$URL" || { echo "ERROR: download failed (wget)"; exit 1; }; fi; '
        'mkdir -p "$TMP/x"; '
        'if command -v unzip >/dev/null 2>&1; then unzip -q -o "$TMP/$ZIP" -d "$TMP/x"; '
        'else python3 -c "import zipfile,sys;zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$TMP/$ZIP" "$TMP/x"; fi; '
        # Release zips as either vuetorrent/ at the root or the files directly.
        'if [ -d "$TMP/x/vuetorrent" ]; then SRC="$TMP/x/vuetorrent"; else SRC="$TMP/x"; fi; '
        '$SUDO rm -rf "$DIR/$DEST"; $SUDO mkdir -p "$DIR/$DEST"; '
        '$SUDO cp -a "$SRC/." "$DIR/$DEST/" || { echo "ERROR: failed to copy into $DIR/$DEST"; exit 1; }; '
        # Keep the downloaded zip alongside the extracted folder in the install
        # dir (operator request — handy for a manual rollback / re-extract).
        '$SUDO cp -f "$TMP/$ZIP" "$DIR/$ZIP" 2>/dev/null || true; '
        'if [ -n "$OWNER" ]; then $SUDO chown -R "$OWNER" "$DIR/$DEST" 2>/dev/null || true; '
        '$SUDO chown "$OWNER" "$DIR/$ZIP" 2>/dev/null || true; fi; '
        'echo "INSTALLED:$DEST"'
    )


async def _run_host_ssh(host_id: Optional[str], command: str,
                        actor_username: Optional[str], *,
                        timeout: float = 120.0) -> dict:
    """Run ONE command on the chip's host over SSH (real run) + write the
    ``ssh_run`` audit row, mirroring the manual SSH-runner route. Returns the
    ``logic.ssh.run_command`` result dict (never raises). SSH must be enabled
    for the host (per-host opt-in + global master switch) or the result carries
    an ``error``."""
    from logic import ssh as _ssh  # noqa: PLC0415
    from logic.db import get_setting  # noqa: PLC0415
    from logic.settings_keys import Settings  # noqa: PLC0415
    try:
        cfg = _json.loads(get_setting(Settings.HOSTS_CONFIG) or "[]")
        if not isinstance(cfg, list):
            cfg = []
    except (ValueError, TypeError):
        cfg = []
    result = await _ssh.run_command(host_id=str(host_id or ""), command=command,
                                    hosts_config=cfg, timeout=timeout)
    try:
        import uuid as _uuid  # noqa: PLC0415
        # NOTE: _ssh_write_audit_row lives in main_pkg.hosts_ssh_routes (the SSH
        # route module), NOT in main — importing from main silently ImportErrors
        # and the ssh_run audit row never gets written.
        # noinspection PyProtectedMember
        from main_pkg.hosts_ssh_routes import _ssh_write_audit_row  # noqa: PLC0415
        _ssh_write_audit_row(op_id=_uuid.uuid4().hex[:8],
                             actor=(actor_username or "ai/telegram"),
                             host_id=str(host_id or ""), command=command, result=result)
    except (ImportError, RuntimeError, OSError, ValueError, TypeError):
        pass  # audit is best-effort — never fail the skill on a logging miss
    return result


# noinspection DuplicatedCode
async def _vuetorrent_check_skill(host_row: dict, chip: dict, *,
                                  host_id: Optional[str] = None) -> dict:
    """Read-only: compare the running VueTorrent WebUI version against the
    latest GitHub release. Never raises."""
    username, password, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[qbittorrent] INFO qbittorrent_vuetorrent_check host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            await _login(cli, base, username, password)
            prefs = await _qbit_prefs(cli, base)
            current, _root = await _current_vuetorrent(cli, base, prefs)
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    latest, _zip, gh_err = await _latest_vuetorrent()
    if gh_err:
        return {"ok": False, "status": 0,
                "detail": f"Current VueTorrent: {current or 'unknown'}. Couldn't "
                          f"check the latest release — {gh_err}."}
    if not current:
        return {"ok": True, "status": 200,
                "detail": f"⚠️ Couldn't detect the running VueTorrent version (is "
                          f"qBittorrent's alternative WebUI enabled?). Latest "
                          f"release is {latest}."}
    if _semver_tuple(current) >= _semver_tuple(latest):
        return {"ok": True, "status": 200,
                "detail": f"✅ VueTorrent is up to date ({current}).",
                "current": current, "latest": latest, "outdated": False}
    return {"ok": True, "status": 200,
            "detail": f"⬆️ VueTorrent {current} is installed — {latest} is "
                      f"available. Run 'Update VueTorrent WebUI' to upgrade.",
            "current": current, "latest": latest, "outdated": True}


# noinspection DuplicatedCode
async def _vuetorrent_update_skill(host_row: dict, chip: dict, *,
                                   host_id: Optional[str] = None,
                                   actor_username: Optional[str] = None) -> dict:
    """Destructive: when VueTorrent is behind, SSH to the host, download +
    extract the latest release next to the current install, and repoint
    qBittorrent's alternative WebUI at it via the API. Never raises."""
    username, password, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[qbittorrent] INFO qbittorrent_vuetorrent_update host={host_id} (live)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            await _login(cli, base, username, password)
            prefs = await _qbit_prefs(cli, base)
            current, root = await _current_vuetorrent(cli, base, prefs)
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    latest, zip_url, gh_err = await _latest_vuetorrent()
    if gh_err:
        return {"ok": False, "status": 0,
                "detail": f"couldn't check the latest release — {gh_err}"}
    if current and _semver_tuple(current) >= _semver_tuple(latest):
        return {"ok": True, "status": 200,
                "detail": f"✅ VueTorrent is already up to date ({current}). Nothing to do."}
    if not root:
        return {"ok": False, "status": 0,
                "detail": "qBittorrent has no alternative WebUI path set, so I can't "
                          "tell where VueTorrent lives. Set Web UI → 'Use alternative "
                          "WebUI' to the VueTorrent folder once, then re-run."}
    install_dir = _posixpath.dirname(root.rstrip("/"))
    new_dir = f"{install_dir}/VueTorrent.{latest}"
    # Validate everything that reaches the shell (defence-in-depth — these come
    # from a GitHub tag + qBittorrent's own prefs, but never trust either).
    if not _STRICT_SEMVER_RE.match(latest):
        return {"ok": False, "status": 0,
                "detail": f"refusing to run — unexpected version '{latest}'"}
    if not _SAFE_PATH_RE.match(install_dir):
        return {"ok": False, "status": 0,
                "detail": f"refusing to run — unsafe install directory '{install_dir}'"}
    if not zip_url.startswith(ExternalURL.GITHUB + "/"):
        return {"ok": False, "status": 0,
                "detail": "refusing to run — the download URL isn't a github.com release"}
    # 1) SSH: download + extract.
    script = _vuetorrent_install_script(install_dir, latest, zip_url)
    ssh_result = await _run_host_ssh(host_id, script, actor_username, timeout=180.0)
    if not ssh_result.get("ok"):
        detail = (ssh_result.get("error") or ssh_result.get("stderr")
                  or "SSH command failed")
        return {"ok": False, "status": 0,
                "detail": f"download/extract over SSH failed: {str(detail)[:300]} "
                          f"(is SSH enabled for this host in Admin → Hosts?)"}
    if "INSTALLED:" not in str(ssh_result.get("stdout") or ""):
        tail = str(ssh_result.get("stdout") or "")[-300:] or "(no output)"
        return {"ok": False, "status": 0,
                "detail": f"the install step didn't confirm success: {tail}"}
    # 2) Repoint qBittorrent's alternative WebUI at the new dir (API — no conf
    #    edit, no restart; qBittorrent persists + applies it).
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            await _login(cli, base, username, password)
            payload = _json.dumps({"alternative_webui_enabled": True,
                                   "alternative_webui_path": new_dir})
            r = await cli.post(base + "/api/v2/app/setPreferences",
                               data={"json": payload},
                               headers={"Referer": base, "Origin": base})
            if r.status_code != 200:
                raise RuntimeError(f"setPreferences returned HTTP {r.status_code}")
    except (httpx.HTTPError, OSError, RuntimeError) as e:
        return {"ok": False, "status": 0,
                "detail": f"installed {latest} to {new_dir} but couldn't repoint "
                          f"qBittorrent ({e}). Set the alternative WebUI path to "
                          f"{new_dir} manually."}
    return {"ok": True, "status": 200,
            "detail": f"✅ Updated VueTorrent {current or '?'} → {latest}. "
                      f"qBittorrent's WebUI now serves from {new_dir}. Reload the "
                      f"WebUI to see it (restart qBittorrent if it doesn't switch)."}


# noinspection DuplicatedCode
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch the current transfer + torrent-count summary
    (force-bypasses the cache). Never raises."""
    print(f"[qbittorrent] INFO qbittorrent_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[qbittorrent] warning: qbittorrent_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    total = safe_int(data.get("torrents_total"))
    dl = safe_int(data.get("downloading"))
    seed = safe_int(data.get("seeding"))
    paused = safe_int(data.get("paused"))
    done = safe_int(data.get("completed"))
    lines = [
        f"⬇️ Download: {_fmt_speed(data.get('dl_speed'))}",
        f"⬆️ Upload: {_fmt_speed(data.get('up_speed'))}",
        f"📥 Downloading: {dl:,}",
        f"🌱 Seeding: {seed:,}",
        f"⏸️ Paused: {paused:,}",
        f"✅ Completed: {done:,}",
        f"📦 Total torrents: {total:,}",
    ]
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "torrents_total": total, "downloading": dl, "seeding": seed,
        "paused": paused, "completed": done,
    }


# Free-text state filter → the buckets _classify emits. Blank → active
# (downloading + seeding); 'all' → every torrent.
def _resolve_list_filter(arg: Optional[str]) -> "tuple[set, str]":
    a = (arg or "").strip().lower()
    if not a:
        return {"downloading", "seeding"}, "active"
    if "all" in a or "everything" in a:
        return {"downloading", "seeding", "paused", "completed", "error", "other"}, "all"
    want: set = set()
    if any(w in a for w in ("download", "leech", "grabb")):
        want.add("downloading")
    if any(w in a for w in ("seed", "upload")):
        want.add("seeding")
    if any(w in a for w in ("pause", "stop")):
        want.add("paused")
    if any(w in a for w in ("complete", "done", "finished")):
        want.add("completed")
    if not want:
        want = {"downloading", "seeding"}
    return want, a


_LIST_TAKE = 30  # cap the listed torrents so a huge client doesn't flood chat

# qBittorrent's "infinite" ETA sentinel (8640000 == 100 days) — treat as "no ETA".
_ETA_INFINITE = 8640000


def _fmt_eta(seconds: Any) -> str:
    """Compact ETA from qBittorrent's ``eta`` (seconds). '' for the infinite
    sentinel / non-positive values."""
    s = safe_int(seconds)
    if s <= 0 or s >= _ETA_INFINITE:
        return ""
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, _sec = divmod(rem, 60)
    if d > 0:
        return f"{d}d {h}h"
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m"
    return f"{s}s"


# noinspection DuplicatedCode
async def _downloading_skill(host_row: dict, chip: dict, *,
                             host_id: Optional[str] = None) -> dict:
    """Read-only: what's currently downloading — name + state + progress % + the
    live download rate (fastest first), rendered with a per-row progress bar in
    the drawer. Plain ``detail`` for AI / Telegram. Never raises."""
    username, password, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[qbittorrent] INFO qbittorrent_downloading host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            await _login(cli, base, username, password)
            r = await cli.get(base + "/api/v2/torrents/info",
                              params={"filter": "downloading"})
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"fetch failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code,
                "detail": "auth failed (check username / password)"}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    try:
        torrents = as_list(r.json())
    except (ValueError, TypeError):
        torrents = []
    # Keep genuinely-downloading torrents (the ?filter=downloading view also
    # includes stalledDL / metaDL — all 'downloading' under _classify); drop
    # any that have already completed.
    active = []
    for t in torrents:
        if not isinstance(t, dict):
            continue
        prog = safe_float(t.get("progress"))
        if _classify(t.get("state")) != "downloading" or prog >= 1.0:
            continue
        active.append(t)
    # Fastest first, then most-complete — the most "alive" downloads on top.
    active.sort(key=lambda tr: (safe_int(tr.get("dlspeed")), safe_float(tr.get("progress"))),
                reverse=True)
    if not active:
        return {"ok": True, "status": 200,
                "detail": "📥 Nothing is downloading right now."}
    lines = []
    rich: list[dict] = []
    for t in active[:_LIST_TAKE]:
        name = str(t.get("name") or "?").strip()
        prog = safe_float(t.get("progress"))
        pct = int(round(min(1.0, max(0.0, prog)) * 100))
        dl = safe_int(t.get("dlspeed"))
        state = str(t.get("state") or "").strip()
        stalled = state.lower().startswith("stalled") or dl <= 0
        eta = _fmt_eta(t.get("eta"))
        # Plain text (AI / Telegram).
        speed_txt = "stalled" if stalled else _fmt_speed(dl)
        lines.append(f"📥 {name} — {pct}% · {speed_txt}"
                     + (f" · ETA {eta}" if eta and not stalled else ""))
        # Rich row: subtitle = ↓ rate (+ ETA), progress bar = pct, and a
        # per-row delete affordance (trash button) carrying THIS torrent's hash
        # so the drawer can remove it (destructive — the SPA confirms first).
        sub = "⏸ stalled" if stalled else ("↓ " + _fmt_speed(dl)
                                           + (f" · ETA {eta}" if eta else ""))
        thash = str(t.get("hash") or "").strip()
        row: "dict[str, Any]" = {"title": name, "subtitle": sub, "progress": pct}
        if thash:
            row["row_action"] = {
                "skill_id": "qbittorrent_delete", "arg": thash,
                "icon": "trash-2", "destructive": True,
                "confirm_i18n": "apps.qbittorrent.delete_confirm",
                "title_i18n": "apps.qbittorrent.delete_title"}
        rich.append(row)
    head = f"📥 Downloading ({len(active)}):"
    return {"ok": True, "status": 200,
            "detail": head + "\n" + "\n".join(lines),
            "count": len(active),
            "count_i18n": "apps.qbittorrent.downloading_now_count",
            "items": rich}


# noinspection DuplicatedCode
async def _list_skill(host_row: dict, chip: dict, *,
                      arg: Optional[str] = None,
                      host_id: Optional[str] = None) -> dict:
    """Read-only (arg): list torrent NAMES + state + progress, optionally
    filtered by state. Never raises."""
    username, password, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    want, label = _resolve_list_filter(arg)
    print(f"[qbittorrent] INFO qbittorrent_list host={host_id} filter={label!r}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            await _login(cli, base, username, password)
            r = await cli.get(base + "/api/v2/torrents/info")
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"list failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code,
                "detail": "auth failed (check username / password)"}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
    try:
        torrents = as_list(r.json())
    except (ValueError, TypeError):
        torrents = []
    rows = []
    for t in torrents:
        if not isinstance(t, dict):
            continue
        prog = safe_float(t.get("progress"))
        live = _classify(t.get("state"))
        bucket = "completed" if prog >= 1.0 else live
        # "completed" filter matches progress==1; the active/etc filters match
        # the live bucket. Keep a completed torrent visible under both.
        if bucket not in want and live not in want:
            continue
        name = str(t.get("name") or "?").strip()
        pct = int(round(min(1.0, max(0.0, prog)) * 100))
        icon = {"downloading": "📥", "seeding": "🌱", "paused": "⏸️",
                "completed": "✅", "error": "⚠️"}.get(live, "•")
        rows.append(f"{icon} {name} — {pct}%")
        if len(rows) >= _LIST_TAKE:
            break
    if not rows:
        return {"ok": True, "status": 200,
                "detail": f"📦 No {label} torrents on qBittorrent right now."}
    head = "📦 Torrents" + (f" ({label})" if label != "active" else " (active)") + f" — {len(rows)}:"
    return {"ok": True, "status": 200, "detail": head + "\n" + "\n".join(rows),
            "count": len(rows)}


async def _add_skill(host_row: dict, chip: dict, *,
                     arg: Optional[str] = None,
                     host_id: Optional[str] = None) -> dict:
    """Action (arg): add a torrent by magnet link or .torrent URL via
    ``POST /api/v2/torrents/add`` (``urls=<arg>``). Never raises."""
    link = (arg or "").strip()
    if not link:
        return {"ok": False, "status": 0,
                "detail": "no magnet / URL given — say e.g. 'add magnet:?xt=… on qbittorrent'"}
    if not (link.lower().startswith("magnet:") or link.lower().startswith("http://")
            or link.lower().startswith("https://")):
        return {"ok": False, "status": 0,
                "detail": "that doesn't look like a magnet: link or an http(s) .torrent URL"}
    username, password, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[qbittorrent] INFO qbittorrent_add host={host_id} link={link[:60]!r}…")
    try:
        async with httpx.AsyncClient(verify=False, timeout=25.0,
                                     follow_redirects=True) as cli:
            await _login(cli, base, username, password)
            r = await cli.post(base + "/api/v2/torrents/add", data={"urls": link})
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"add failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code,
                "detail": "auth failed (check username / password)"}
    # qBittorrent returns 200 + "Ok." on success, 415 when the payload isn't a
    # valid torrent / magnet.
    if r.status_code == 200 and not (r.text or "").strip().lower().startswith("fail"):
        return {"ok": True, "status": 200, "detail": "➕ Added the torrent to qBittorrent."}
    if r.status_code == 415:
        return {"ok": False, "status": 415,
                "detail": "qBittorrent rejected it (not a valid torrent / magnet)"}
    return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}


_HASH_RE = _re.compile(r"^[A-Fa-f0-9]{40}$|^[A-Za-z0-9]{32}$")


async def _delete_skill(host_row: dict, chip: dict, *,
                        arg: Optional[str] = None,
                        host_id: Optional[str] = None) -> dict:
    """Destructive action: delete ONE torrent by its hash (also removes the
    downloaded files — a partial download's data is useless). The drawer's
    per-row trash button supplies the hash; gated by the standard destructive
    confirm. Never raises."""
    h = (arg or "").strip()
    if not _HASH_RE.match(h):
        return {"ok": False, "status": 0,
                "detail": "no valid torrent hash given (the trash button supplies it)"}
    username, password, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[qbittorrent] INFO qbittorrent_delete host={host_id} hash={h[:12]}…")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            await _login(cli, base, username, password)
            r = await cli.post(base + "/api/v2/torrents/delete",
                               data={"hashes": h.lower(), "deleteFiles": "true"})
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"delete failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code,
                "detail": "auth failed (check username / password)"}
    if 200 <= r.status_code < 300:
        return {"ok": True, "status": 200, "detail": "🗑️ Deleted the torrent (and its files)."}
    return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}


async def _set_all_skill(host_row: dict, chip: dict, *, resume: bool,
                         host_id: Optional[str] = None) -> dict:
    """Action: pause (stop) OR resume (start) EVERY torrent (``hashes=all``).
    Tries the 4.x endpoint name first, falls back to the 5.x rename on 404.
    Never raises."""
    username, password, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    verb = "resume" if resume else "pause"
    endpoints = (("resume", "start") if resume else ("pause", "stop"))
    print(f"[qbittorrent] INFO qbittorrent_{verb}_all host={host_id}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            await _login(cli, base, username, password)
            r = await cli.post(base + f"/api/v2/torrents/{endpoints[0]}",
                               data={"hashes": "all"})
            if r.status_code in (404, 405):
                # 5.x renamed pause→stop / resume→start.
                r = await cli.post(base + f"/api/v2/torrents/{endpoints[1]}",
                                   data={"hashes": "all"})
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"{verb} failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code,
                "detail": "auth failed (check username / password)"}
    if 200 <= r.status_code < 300:
        msg = ("▶️ Resumed all torrents on qBittorrent." if resume
               else "⏸️ Paused all torrents on qBittorrent.")
        return {"ok": True, "status": 200, "detail": msg}
    return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}
