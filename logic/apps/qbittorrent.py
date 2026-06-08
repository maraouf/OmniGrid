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

import re as _re
import time
from typing import Any, Optional

import httpx

from logic.apps._common import resolve_base_url, resolve_cache_ttl
from logic.apps._common import cache_key, peek_cache
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


# noinspection DuplicatedCode
# The two-field (username + secret api_key) credential resolver is the
# deliberate per-app twin of AdGuard's `_creds` (the per-app encapsulation
# pattern, CLAUDE.md) — the username+password auth shape stays inline per
# module rather than coupling them through a shared helper.
def _creds(chip: dict, *, password: Optional[str] = None,
           username: Optional[str] = None) -> "tuple[str, str]":
    """Resolve ``(username, password)`` for a chip. Explicit args win (a
    pre-save test passes the candidate values); else fall back to the stored
    chip fields (``username`` plain, ``api_key`` = the password)."""
    u = (username if username is not None else "").strip() or (chip.get("username") or "").strip()
    p = (password if password is not None else "").strip() or (chip.get("api_key") or "").strip()
    return u, p


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
    username, password = _creds(
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
    username, password = _creds(chip)
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
    torrents = as_list(tor.json()) if tor.status_code == 200 else []
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
    raise ValueError(f"unknown skill: {skill_id!r}")


def _resolve_target(host_row: dict, chip: dict) -> "tuple[str, str, str, Optional[dict]]":
    """Resolve ``(username, password, base)`` or return a ready
    ``{ok: False, detail}`` when the base URL won't resolve."""
    username, password = _creds(chip)
    base = resolve_base_url(host_row, chip)
    if not base:
        return "", "", "", {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    return username, password, base, None


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
