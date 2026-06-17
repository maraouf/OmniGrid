"""Tracearr per-app module.

Encapsulates everything Tracearr-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Tracearr (github.com/connorgallopo/
Tracearr) is a Tautulli-style monitoring dashboard for Plex / Jellyfin / Emby —
real-time streams, playback analytics, and account-sharing ("violation")
detection across multiple media servers from one dashboard. It is a member of
the per-app family in SHAPE (SLUGS / requires_api_key / test_credential /
fetch_data / peek_latest / SKILLS / run_skill) but BESPOKE, NOT a *arr — its
auth model differs:

  Auth model — a PUBLIC-API BEARER TOKEN (not an X-Api-Key header, not a query
    param). Generate the token in Tracearr → Settings → API; it is prefixed
    ``trr_pub_`` and rides ``Authorization: Bearer <token>`` on every call. The
    read-only Public API lives under ``<base>/api/v1/public`` (Swagger at
    ``/api-docs``). The key is stored per-instance + write-only (the SPA only
    ever sees ``api_key_set: bool``). Stateless — the token goes on every
    request, so the module is correct on rotation.

The expanded card answers "what's my media fleet doing right now" at a glance,
from ``GET /api/v1/public/stats`` (+ ``/health`` for the server roster):

    active_streams    — currently active playback sessions   (/stats)
    total_users       — distinct users across all servers     (/stats)
    total_sessions    — plays in the last 30 days              (/stats)
    recent_violations — account-sharing violations, last 7d    (/stats)
    servers_online /   — media servers reporting healthy / total
    servers_total                                              (/health)
    version           — Tracearr version (best-effort)          (/health)

The ``/stats`` call is the load-bearing one (confirms the token works);
``/health`` is tolerated (an empty server roster never fails the fetch).

AI / Telegram skills (all read-only — Tracearr is a monitor):
* ``tracearr_status``     — fleet activity + violation summary (live fetch).
* ``tracearr_streams``    — who's watching what right now (rich poster list).
* ``tracearr_servers``    — media servers + online state + active streams each.
* ``tracearr_violations`` — recent account-sharing violations.

Single-instance app (NOT fleet) — one card per pinned chip.

Upstream API reference: ``<base>/api/v1/public/<endpoint>``
    GET /stats       — {activeStreams, totalUsers, totalSessions, recentViolations}
    GET /health      — {status, version, servers:[{id,name,type,online,activeStreams}]}
    GET /streams     — {data:[{username, userAvatarUrl, mediaTitle, mediaType,
                              showTitle, seasonNumber, episodeNumber, posterUrl,
                              durationMs, progressMs, state, isTranscode, ...}],
                        summary:{total, transcodes, directStreams, directPlays}}
    GET /violations  — {data:[{severity, user:{username,thumbUrl}, rule:{type,name},
                              serverName, createdAt}], meta:{total}}
Poster / avatar URLs from /streams are Tracearr's own ``/api/v1/images/proxy``
RELATIVE paths (a PUBLIC route — no token needed) or absolute gravatar / plex.tv
avatars; both are routed through the per-app image proxy so a cross-origin
avatar still loads.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import httpx
from logic.external_urls import ExternalURL

from logic.apps._common import (
    cache_key, peek_cache, resolve_base_url, resolve_cache_ttl, resolve_credential_target)
from logic.coerce import as_dict, as_list, safe_int

# Catalog template slugs handled by this module.
SLUGS: tuple[str, ...] = ("tracearr",)

# Public API base path — every read-only endpoint lives under here.
_PUB = "/api/v1/public"

# Per-(host_id, service_idx) data cache for the expanded card. 60s default —
# matches the rest of the family.
DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}

# Account-sharing violation severity → emoji for the violations skill.
_SEVERITY_EMOJI = {"high": "🚨", "warning": "⚠️", "low": "ℹ️"}

# Absolute avatar hosts the per-app image proxy will fetch (Tracearr's
# buildAvatarUrl emits a gravatar fallback or a plex.tv avatar for Plex users).
_AVATAR_PROXY_HOSTS = (
    ExternalURL.GRAVATAR_HOST, ExternalURL.PLEX_TV_HOST, ExternalURL.PLEX_DIRECT_HOST)


def requires_api_key() -> bool:
    """Tracearr's Public API needs the ``trr_pub_`` bearer token."""
    return True


def _headers(token: str) -> dict:
    """Standard Tracearr auth headers — the public-API bearer token + a JSON
    Accept. Shared by every call so the header shape lives in one place."""
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


async def _call(cli: httpx.AsyncClient, base: str, token: str,
                endpoint: str, **params: Any) -> Any:
    """One Tracearr public-API call: ``GET <base>/api/v1/public/<endpoint>``.

    Returns the parsed JSON body on success. Raises ``RuntimeError`` on a
    transport error, an auth failure (401 / 403 → bad / missing token), a
    non-200 status, or non-JSON. Tracearr uses real HTTP status codes (unlike
    Tautulli's always-200 shape), so the status IS the truth."""
    url = base.rstrip("/") + _PUB + "/" + endpoint.lstrip("/")
    qp = {k: v for k, v in params.items() if v is not None}
    try:
        r = await cli.get(url, headers=_headers(token), params=qp)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        raise RuntimeError(f"request failed: {type(e).__name__}: {e}")
    if r.status_code in (401, 403):
        raise RuntimeError("auth failed: invalid or missing API token (check api_key)")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} for {endpoint}")
    try:
        return r.json()
    except (ValueError, TypeError):
        raise RuntimeError("non-JSON from upstream")


def image_proxy_url(host_row: dict, chip: dict, path: str) -> "tuple[str, dict]":
    """Per-app image-proxy hook — turn a Tracearr poster / avatar reference into
    an absolute URL the OmniGrid server fetches.

    Tracearr's ``/streams`` returns ``posterUrl`` / ``userAvatarUrl`` as either:
      - a RELATIVE ``/api/v1/images/proxy?...`` path on Tracearr itself (a
        PUBLIC image route — no token needed) → joined to the chip's OWN base,
        fetched anonymously; OR
      - an absolute gravatar / plex.tv avatar URL (the Plex-user fallback the
        browser can't hotlink) → allowlisted host, fetched anonymously.

    SSRF guard: an absolute URL on any OTHER host is rejected, and a relative
    path must be a clean leading-``/`` path (no traversal)."""
    from urllib.parse import urlsplit  # noqa: PLC0415
    p = (path or "").strip()
    if not p:
        raise ValueError("empty image path")
    if "://" in p:
        host = (urlsplit(p).hostname or "").lower()
        if not any(host == h or host.endswith("." + h) for h in _AVATAR_PROXY_HOSTS):
            raise ValueError(f"image host not allowed: {host}")
        return p, {"Accept": "*/*"}
    if not p.startswith("/") or ".." in p:
        raise ValueError("image must be a clean Tracearr path")
    base = resolve_base_url(host_row, chip)
    if not base:
        raise ValueError("no upstream URL configured")
    return base.rstrip("/") + p, {"Accept": "*/*"}


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe Tracearr by calling ``GET /api/v1/public/stats`` with the supplied
    bearer token. Returns ``{ok, detail, status}`` for direct SPA consumption.
    Falls back to the chip's stored ``api_key`` when ``candidate_key`` is blank
    so a re-test after first save doesn't need a retype."""
    key, base, err = resolve_credential_target(host_row, chip, candidate_key)
    if err:
        return err
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            stats = await _call(cli, base, key, "stats")
            version = await _fetch_version(cli, base, key)
    except RuntimeError as e:
        return {"ok": False, "detail": str(e), "status": 0}
    streams = safe_int((stats or {}).get("activeStreams"))
    detail = f"OK (Tracearr {version})" if version else "OK"
    detail += f" — {streams} active stream{'s' if streams != 1 else ''}"
    return {"ok": True, "detail": detail, "status": 200}


async def _fetch_version(cli: httpx.AsyncClient, base: str, token: str) -> str:
    """Best-effort Tracearr version via ``GET /health``. ``""`` on any failure
    (never load-bearing)."""
    try:
        health = await _call(cli, base, token, "health")
    except RuntimeError:
        return ""
    if isinstance(health, dict):
        return str(health.get("version") or "").strip()
    return ""


def _health_servers(health: Any) -> list:
    """Normalise ``/health``'s server roster → ``[{name, type, online,
    active}]``. Empty list on any unexpected shape (tolerated)."""
    if not isinstance(health, dict):
        return []
    servers = health.get("servers")
    if not isinstance(servers, list):
        return []
    out = []
    for s in servers:
        if not isinstance(s, dict):
            continue
        out.append({
            "name": str(s.get("name") or "?").strip(),
            "type": str(s.get("type") or "").strip().lower(),
            "online": bool(s.get("online")),
            "active": safe_int(s.get("activeStreams")),
        })
    return out


def _shape_activity(activity: dict) -> "tuple[list, dict, list]":
    """Normalise ``/activity`` into ``(plays_series, quality, platforms)`` for
    the drawer chart + breakdowns:
      - ``plays_series``: the last 30 plays-over-time bucket counts (the chart).
      - ``quality``: the playback split ``{direct_play, direct_stream,
        transcode, total}`` (Tracearr's ``quality`` is a PLAYBACK-TIER
        breakdown — Direct Play / Direct Stream / Transcode — NOT a 4k/1080p
        resolution split; Tracearr's API has no historical resolution pie).
      - ``platforms``: top 5 ``{name, count}`` by play count."""
    plays = [safe_int(p.get("count")) for p in as_list(activity.get("plays"))
             if isinstance(p, dict)]
    q = as_dict(activity.get("quality"))
    quality = {
        "direct_play": safe_int(q.get("directPlay")),
        "direct_stream": safe_int(q.get("directStream")),
        "transcode": safe_int(q.get("transcode")),
        "total": safe_int(q.get("total")),
    }
    plats = []
    for p in as_list(activity.get("platforms")):
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or p.get("platform") or "?").strip()
        plats.append({"name": name, "count": safe_int(p.get("count") or p.get("value"))})
    plats.sort(key=lambda x: x["count"], reverse=True)
    return plays[-30:], quality, plats[:5]


def _safe_trend(host_id: str, service_idx: int) -> dict:
    """Best-effort violation + concurrency trend from the Tracearr sampler.
    Returns the ``trend_summary`` dict, or ``{}`` on any failure (a missing
    sampler / empty table must never fail the card)."""
    try:
        from logic.apps import tracearr_sampler  # noqa: PLC0415
        return tracearr_sampler.trend_summary(str(host_id or ""), int(service_idx or 0))
    except Exception as e:  # noqa: BLE001
        print(f"[tracearr] warning: trend_summary failed — {type(e).__name__}: {e}")
        return {}


def _violation_rate(recent_violations: int, total_sessions: int) -> float:
    """Violations per 100 plays (the normalised "how abusive is the traffic"
    figure). 0.0 when there's no play history to divide by."""
    return round(recent_violations * 100.0 / total_sessions, 1) if total_sessions else 0.0


def _fmt_kbps(kbps: Any) -> str:
    """Render a kbps figure as a human rate (kbps / Mbps / Gbps). ``""`` for
    missing / non-positive. Used for the per-server bandwidth rollup (the card's
    fleet total is already a pre-formatted string from Tracearr)."""
    k = safe_int(kbps)
    if k <= 0:
        return ""
    if k < 1000:
        return f"{k:,} kbps"
    mbps = k / 1000.0
    if mbps < 1000:
        return f"{mbps:,.1f} Mbps"
    return f"{mbps / 1000.0:,.1f} Gbps"


def _shape_per_server_bandwidth(streams_data: Any) -> list:
    """Group the active-stream rows by ``serverName`` and sum each stream's
    ``bitrate`` (kbps) → ``[{name, bitrate_kbps, label, streams}]`` busiest-first.
    Tracearr's stream rows carry a per-stream ``bitrate`` (nullable kbps) +
    ``serverName``, so per-server bandwidth needs NO extra call — it's derived
    from the ``/streams`` response the card already fetches. ``[]`` when no
    stream carries a positive bitrate (e.g. all direct-play with no rate)."""
    by_server: dict = {}
    counts: dict = {}
    for s in as_list(streams_data):
        sd = as_dict(s)
        name = str(sd.get("serverName") or "?").strip() or "?"
        rate = safe_int(sd.get("bitrate"))
        by_server[name] = by_server.get(name, 0) + max(0, rate)
        counts[name] = counts.get(name, 0) + 1
    out = []
    for name, kbps in by_server.items():
        if kbps <= 0:
            continue
        out.append({"name": name, "bitrate_kbps": kbps,
                    "label": _fmt_kbps(kbps), "streams": counts.get(name, 0)})
    out.sort(key=lambda x: x["bitrate_kbps"], reverse=True)
    return out


def _shape_violation_rollup(violations_data: Any, *, total: int = 0) -> dict:
    """Roll up a page of recent ``/violations`` rows into ``{top_offenders,
    top_types, sampled, capped}``. ``top_offenders`` is ``[{name, count}]`` by
    ``user.username`` (busiest first); ``top_types`` is ``[{name, count}]`` by the
    violation ``rule.name`` (falling back to ``rule.type``). Tracearr exposes no
    aggregation endpoint, so this tallies the latest page (pageSize max 100,
    newest-first); ``capped`` flags that the upstream total exceeds the page so
    the rollup is over the most-recent slice. ``{...: [], sampled: 0,
    capped: False}`` on an empty / unexpected shape."""
    offenders: dict = {}
    types: dict = {}
    sampled = 0
    for v in as_list(violations_data):
        vd = as_dict(v)
        user = str(as_dict(vd.get("user")).get("username") or "").strip()
        rule = as_dict(vd.get("rule"))
        rname = str(rule.get("name") or rule.get("type") or "").strip()
        if user:
            offenders[user] = offenders.get(user, 0) + 1
        if rname:
            types[rname] = types.get(rname, 0) + 1
        sampled += 1
    top_offenders = [{"name": n, "count": c} for n, c in
                     sorted(offenders.items(), key=lambda kv: kv[1], reverse=True)][:5]
    top_types = [{"name": n, "count": c} for n, c in
                 sorted(types.items(), key=lambda kv: kv[1], reverse=True)][:5]
    return {"top_offenders": top_offenders, "top_types": top_types,
            "sampled": sampled, "capped": bool(total and total > sampled)}


# noinspection DuplicatedCode
# The upstream-error guard + cache block below is structurally shared with every
# other per-app module's fetch_data — the deliberate per-app encapsulation
# pattern (CLAUDE.md). Content differs (Tracearr bearer auth, endpoints,
# fields), so it stays inline rather than coupling modules.
async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch Tracearr's fleet activity summary for the expanded card.

    Returns ``{available, active_streams, total_users, total_sessions,
    recent_violations, servers_total, servers_online, servers, version,
    fetched_at}``. Raises ``ValueError`` / ``RuntimeError`` when the chip's
    api_key is unset / the base URL won't resolve / auth fails / the stats call
    errors. ``/stats`` is load-bearing; ``/health`` is tolerated (empty roster
    when unavailable)."""
    api_key = (chip.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("api_key not set")
    base = resolve_base_url(host_row, chip)
    if not base:
        raise ValueError("no upstream URL configured")
    now = time.time()
    ttl = resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S)
    ck = cache_key(host_id, service_idx)
    cached = _data_cache.get(ck)
    if cached and not force and (now - cached[0]) < ttl:
        return cached[1]
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            stats = await _call(cli, base, api_key, "stats")
            # Server roster — best-effort (an empty / odd /health never fails).
            servers: list = []
            version = ""
            try:
                health = await _call(cli, base, api_key, "health")
                servers = _health_servers(health)
                if isinstance(health, dict):
                    version = str(health.get("version") or "").strip()
            except RuntimeError:
                servers = []
            # Stream summary — transcodes + direct-play/stream split + total
            # bandwidth (a pre-formatted string from Tracearr's formatBitrate).
            # Per-server bandwidth is derived from the SAME response's stream rows
            # (each carries a per-stream bitrate kbps + serverName) — zero extra
            # calls. Best-effort; an empty summary never fails the card.
            summary: dict = {}
            per_server_bw: list = []
            try:
                sdata = await _call(cli, base, api_key, "streams", summary="true")
                if isinstance(sdata, dict):
                    if isinstance(sdata.get("summary"), dict):
                        summary = sdata["summary"]
                    per_server_bw = _shape_per_server_bandwidth(sdata.get("data"))
            except RuntimeError:
                summary = {}
                per_server_bw = []
            # Violation rollup — top-offending users + top violation types from the
            # latest page of /violations (Tracearr has no aggregation endpoint, so
            # tally the newest page client-side; pageSize max 100). Best-effort —
            # the only raising call is _call, caught below.
            try:
                vdata = as_dict(await _call(cli, base, api_key, "violations", pageSize=100))
                viol_rollup = _shape_violation_rollup(
                    vdata.get("data"),
                    total=safe_int(as_dict(vdata.get("meta")).get("total")))
            except RuntimeError:
                viol_rollup = {"top_offenders": [], "top_types": [], "sampled": 0,
                               "capped": False}
            # Activity (last 30 days) — the plays-over-time series for the drawer
            # chart + the playback-quality breakdown (Direct Play / Direct Stream
            # / Transcode) + the top platforms. Best-effort; an empty activity
            # never fails the card.
            activity: dict = {}
            try:
                activity = as_dict(await _call(cli, base, api_key, "activity", period="month"))
            except RuntimeError:
                activity = {}
    except RuntimeError as e:
        print(f"[tracearr] error: fetch host={host_id} — {e}")
        raise RuntimeError(str(e))
    st = stats if isinstance(stats, dict) else {}
    servers_online = sum(1 for s in servers if s.get("online"))
    plays_series, quality, platforms = _shape_activity(activity)
    out: dict[str, Any] = {
        "available": True,
        "active_streams": safe_int(st.get("activeStreams")),
        "transcodes": safe_int(summary.get("transcodes")),
        "direct_streams": safe_int(summary.get("directStreams")),
        "direct_plays": safe_int(summary.get("directPlays")),
        "bandwidth": str(summary.get("totalBitrate") or "").strip(),
        "total_users": safe_int(st.get("totalUsers")),
        "total_sessions": safe_int(st.get("totalSessions")),
        "recent_violations": safe_int(st.get("recentViolations")),
        # Violations per 100 plays (live) — the P1 abuse-intensity stat.
        "violation_rate": _violation_rate(safe_int(st.get("recentViolations")),
                                          safe_int(st.get("totalSessions"))),
        # Violation + concurrency trend from the local sampler (empty when the
        # table has no rows yet — a fresh deploy / just-enabled sampler).
        "trend": _safe_trend(str(host_id or ""), int(service_idx or 0)),
        "servers_total": len(servers),
        "servers_online": servers_online,
        "servers": servers,
        # Per-server bandwidth (from the active-stream rows' bitrate) — drawer.
        "per_server_bandwidth": per_server_bw,
        # Violation rollup (recent): top-offending users + top violation types.
        "top_offenders": viol_rollup["top_offenders"],
        "top_offender": (viol_rollup["top_offenders"][0]
                         if viol_rollup["top_offenders"] else {}),
        "violation_types": viol_rollup["top_types"],
        "top_violation_type": (viol_rollup["top_types"][0]
                               if viol_rollup["top_types"] else {}),
        "violation_rollup_sampled": viol_rollup["sampled"],
        "violation_rollup_capped": viol_rollup["capped"],
        "plays_series": plays_series,
        "quality": quality,
        "platforms": platforms,
        "version": version,
        "fetched_at": int(now),
    }
    print(f"[tracearr] INFO fetched host={host_id} streams={out['active_streams']} "
          f"transcodes={out['transcodes']} bw={out['bandwidth'] or '-'} "
          f"users={out['total_users']} plays30d={out['total_sessions']} "
          f"violations7d={out['recent_violations']} "
          f"servers={servers_online}/{out['servers_total']} "
          f"top_offender={(out['top_offender'] or {}).get('name') or '-'} "
          f"top_viol_type={(out['top_violation_type'] or {}).get('name') or '-'} "
          f"(rollup n={out['violation_rollup_sampled']}"
          f"{'+' if out['violation_rollup_capped'] else ''}) "
          f"per_server_bw={len(out['per_server_bandwidth'])}")
    _data_cache[ck] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "active_streams": safe_int(data.get("active_streams")),
        "transcodes": safe_int(data.get("transcodes")),
        "bandwidth": data.get("bandwidth") or "",
        "total_users": safe_int(data.get("total_users")),
        "total_sessions": safe_int(data.get("total_sessions")),
        "recent_violations": safe_int(data.get("recent_violations")),
        "violation_rate": data.get("violation_rate") or 0.0,
        "top_offender": (as_dict(data.get("top_offender")).get("name") or ""),
        "top_violation_type": (as_dict(data.get("top_violation_type")).get("name") or ""),
        "servers_online": safe_int(data.get("servers_online")),
        "servers_total": safe_int(data.get("servers_total")),
        "version": data.get("version") or "",
        "fetched_at": safe_int(data.get("fetched_at")),
    }


SKILLS: tuple[dict, ...] = (
    {
        "id": "tracearr_status",
        "name": "Tracearr status",
        "ai_phrases": ("tracearr status, media fleet activity, how many streams, "
                       "plex jellyfin emby activity, who is watching, active "
                       "streams across servers, account sharing summary, "
                       "tracearr summary"),
        "destructive": False,
    },
    {
        "id": "tracearr_streams",
        "name": "Who's watching now",
        "ai_phrases": ("who is watching now, current streams, what is playing "
                       "right now, active playback, now playing on tracearr, "
                       "who's streaming, current activity"),
        "destructive": False,
    },
    {
        "id": "tracearr_servers",
        "name": "List media servers",
        "ai_phrases": ("list media servers, what servers are monitored, plex "
                       "jellyfin emby servers, server health, are my servers "
                       "online, tracearr servers"),
        "destructive": False,
    },
    {
        "id": "tracearr_violations",
        "name": "Recent violations",
        "ai_phrases": ("account sharing violations, recent violations, who is "
                       "sharing their account, password sharing, flagged users, "
                       "tracearr violations, sharing detections"),
        "destructive": False,
    },
    {
        "id": "tracearr_terminate",
        "name": "Terminate a stream",
        # arg = the stream's session UUID. arg:True keeps it OUT of the drawer's
        # one-click button list (it's driven by the per-stream row action + the
        # AI supplying the id) — see the arg-skill rule in CLAUDE.md.
        "ai_phrases": ("terminate a stream, stop a stream, kill a stream, end "
                       "playback, stop someone watching, terminate session"),
        "destructive": True,
        "arg": True,
    },
)


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None,
                    arg: Optional[str] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Raises ValueError on an unknown id."""
    if skill_id == "tracearr_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "tracearr_streams":
        return await _streams_skill(host_row, chip, host_id=host_id)
    if skill_id == "tracearr_servers":
        return await _servers_skill(host_row, chip, host_id=host_id)
    if skill_id == "tracearr_violations":
        return await _violations_skill(host_row, chip, host_id=host_id)
    if skill_id == "tracearr_terminate":
        return await _terminate_skill(host_row, chip, arg=arg, host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


def _resolve_target(host_row: dict, chip: dict) -> "tuple[str, str, Optional[dict]]":
    """Resolve ``(api_key, base)`` or return a ready ``{ok: False, detail}`` —
    the Tracearr analogue of the shared ``resolve_skill_target`` (Tracearr
    doesn't use ``_servarr``)."""
    api_key = (chip.get("api_key") or "").strip()
    if not api_key:
        return "", "", {"ok": False, "status": 0, "detail": "Tracearr api_key not set"}
    base = resolve_base_url(host_row, chip)
    if not base:
        return "", "", {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    return api_key, base, None


# noinspection DuplicatedCode
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch the current fleet activity + violation summary
    (force-bypasses the cache). Never raises."""
    print(f"[tracearr] INFO tracearr_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip,
                                host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0),
                                force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[tracearr] warning: tracearr_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    streams = safe_int(data.get("active_streams"))
    transcodes = safe_int(data.get("transcodes"))
    bw = str(data.get("bandwidth") or "").strip()
    users = safe_int(data.get("total_users"))
    plays = safe_int(data.get("total_sessions"))
    viol = safe_int(data.get("recent_violations"))
    s_on = safe_int(data.get("servers_online"))
    s_tot = safe_int(data.get("servers_total"))
    lines = [
        f"▶️ Active streams: {streams:,}"
        + (f" ({transcodes:,} transcoding)" if streams else ""),
    ]
    if bw:
        lines.append(f"📶 Bandwidth: {bw}")
    rate = data.get("violation_rate") or 0.0
    lines += [
        f"🖥️ Servers online: {s_on:,}/{s_tot:,}",
        f"👥 Users: {users:,}",
        f"📊 Plays (30d): {plays:,}",
        f"🚨 Violations (7d): {viol:,}"
        + (f" ({rate:g} per 100 plays)" if rate else ""),
    ]
    offender = as_dict(data.get("top_offender"))
    if offender.get("name"):
        lines.append(f"⛔ Top offender: {offender.get('name')}"
                     + (f" ({safe_int(offender.get('count')):,} violations)"
                        if offender.get("count") else ""))
    vtype = as_dict(data.get("top_violation_type"))
    if vtype.get("name"):
        lines.append(f"🔁 Top violation type: {vtype.get('name')}"
                     + (f" ({safe_int(vtype.get('count')):,})" if vtype.get("count") else ""))
    return {
        "ok": True,
        "detail": "\n".join(lines),
        "status": 200,
        "active_streams": streams, "transcodes": transcodes, "bandwidth": bw,
        "total_users": users, "total_sessions": plays, "recent_violations": viol,
        "violation_rate": rate,
        "top_offender": offender.get("name") or "",
        "top_violation_type": vtype.get("name") or "",
        "servers_online": s_on, "servers_total": s_tot,
    }


def _fmt_ms(ms: Any) -> str:
    """Render a millisecond duration as ``m:ss`` (or ``h:mm:ss`` past an hour)."""
    total = max(0, safe_int(ms) // 1000)
    h, rem = divmod(total, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _playback_label(s: dict) -> str:
    """Tracearr's playback decision for one stream: Transcode (either track is
    being transcoded), Direct Stream (container remux — a ``copy`` decision), or
    Direct Play. Mirrors Tracearr's own ``categorizeStream`` rule."""
    if s.get("isTranscode"):
        return "Transcode"
    vd = str(s.get("videoDecision") or "").strip().lower()
    ad = str(s.get("audioDecision") or "").strip().lower()
    if vd == "copy" or ad == "copy":
        return "Direct Stream"
    return "Direct Play"


def _stream_title_sub(s: dict) -> "tuple[str, str]":
    """Build the rich-item ``(title, subtitle)`` for one active stream. Episodes
    lead with the show + ``SxxEyy``; music leads with the artist + album; movies
    are the bare title (+ year)."""
    mtype = str(s.get("mediaType") or "").strip().lower()
    title = str(s.get("mediaTitle") or "?").strip()
    sub_parts: list = []
    if mtype == "episode":
        show = str(s.get("showTitle") or "").strip()
        se = safe_int(s.get("seasonNumber"))
        ep = safe_int(s.get("episodeNumber"))
        if show:
            tag = ""
            if se or ep:
                tag = f" S{se:02d}E{ep:02d}" if (se and ep) else (f" S{se:02d}" if se else f" E{ep:02d}")
            title = show
            sub_parts.append((str(s.get("mediaTitle") or "").strip() + tag).strip() or tag.strip())
    elif mtype == "track":
        artist = str(s.get("artistName") or "").strip()
        album = str(s.get("albumName") or "").strip()
        if artist:
            title = artist
        if album:
            sub_parts.append(album)
    else:
        yr = str(s.get("year") or "").strip()
        if len(yr) >= 4 and yr[:4].isdigit():
            sub_parts.append(yr[:4])
    return title, " · ".join(p for p in sub_parts if p)


# noinspection DuplicatedCode
async def _streams_skill(host_row: dict, chip: dict, *,
                         host_id: Optional[str] = None) -> dict:
    """Read-only: who's watching what right now (rich poster list) from
    ``GET /streams``. Never raises."""
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[tracearr] INFO tracearr_streams host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            data = await _call(cli, base, api_key, "streams")
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    rows = data.get("data") if isinstance(data, dict) else None
    streams = rows if isinstance(rows, list) else []
    rich: list[dict] = []
    lines: list[str] = []
    for s in streams[:15]:
        if not isinstance(s, dict):
            continue
        title, subtitle = _stream_title_sub(s)
        user = str(s.get("username") or "?").strip()
        server = str(s.get("serverName") or "").strip()
        state = str(s.get("state") or "").strip().lower()
        playback = _playback_label(s)  # Direct Play / Direct Stream / Transcode
        resolution = str(s.get("resolution") or "").strip()
        dur = safe_int(s.get("durationMs"))
        prog = safe_int(s.get("progressMs"))
        time_str = f"{_fmt_ms(prog)} / {_fmt_ms(dur)}" if dur > 0 else ""
        # Subtitle: <media-sub> · <resolution> · <playback> · <pause state> · <time/total>
        meta: list = [p for p in [subtitle, resolution, playback] if p]
        if state and state not in ("playing", ""):
            meta.append(state)  # e.g. "paused", "buffering"
        if time_str:
            meta.append(time_str)
        sub2 = " · ".join(meta)
        row: "dict[str, Any]" = {"title": title, "subtitle": sub2}
        if user and user != "?":
            row["byline"] = user + (f" · {server}" if server else "")
        poster = str(s.get("posterUrl") or "").strip()
        if poster:
            row["poster"] = poster
            row["poster_proxy"] = True
        avatar = str(s.get("userAvatarUrl") or "").strip()
        if avatar:
            row["avatar"] = avatar
            row["avatar_proxy"] = True
        if dur > 0 and prog >= 0:
            row["progress"] = max(0, min(100, round(prog * 100.0 / dur)))
        # Per-row STOP action — terminate this stream (destructive; the backend
        # gates on confirm + the SPA shows a typed confirm). The stream id is a
        # session UUID.
        sid = str(s.get("id") or "").strip()
        if sid:
            row["row_action"] = {
                "skill_id": "tracearr_terminate", "arg": sid,
                "icon": "circle-off", "destructive": True,
                "confirm_i18n": "apps.tracearr.terminate_confirm",
                "title_i18n": "apps.tracearr.terminate_title"}
        rich.append(row)
        lines.append(f"• {user}{' (' + server + ')' if server else ''} — {title}"
                     + (f" — {sub2}" if sub2 else ""))
    if not rich:
        return {"ok": True, "status": 200, "detail": "▶️ Nothing is playing right now."}
    return {"ok": True, "status": 200,
            "detail": "▶️ Now playing:\n" + "\n".join(lines),
            "count": len(rich), "count_i18n": "apps.tracearr.streams_count",
            "items": rich}


async def _terminate_skill(host_row: dict, chip: dict, *,
                           arg: Optional[str] = None,
                           host_id: Optional[str] = None) -> dict:
    """Destructive: terminate an active stream via
    ``POST /api/v1/public/streams/<id>/terminate``. ``arg`` is the stream's
    session UUID (from the streams list's per-row action). Never raises — the
    backend route already gated the destructive-confirm."""
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    sid = str(arg or "").strip()
    if not sid:
        return {"ok": False, "status": 0, "detail": "no stream id given"}
    from urllib.parse import quote  # noqa: PLC0415
    url = base.rstrip("/") + _PUB + f"/streams/{quote(sid, safe='')}/terminate"
    print(f"[tracearr] INFO tracearr_terminate host={host_id} session={sid}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.post(url, headers=_headers(api_key),
                               json={"reason": "Stopped from OmniGrid"})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"terminate failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code, "detail": "auth failed (check api_key)"}
    if r.status_code == 404:
        return {"ok": True, "status": 200, "detail": "⏹️ That stream is no longer active."}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code} terminating stream"}
    return {"ok": True, "status": 200, "detail": "⏹️ Stream terminated."}


async def _servers_skill(host_row: dict, chip: dict, *,
                         host_id: Optional[str] = None) -> dict:
    """Read-only: the monitored media servers + online state + active streams
    each, from ``GET /health``. Never raises."""
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[tracearr] INFO tracearr_servers host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            health = await _call(cli, base, api_key, "health")
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    servers = _health_servers(health)
    if not servers:
        return {"ok": True, "status": 200, "detail": "🖥️ No media servers reported."}
    lines = []
    for s in servers:
        dot = "🟢" if s.get("online") else "🔴"
        typ = str(s.get("type") or "").strip()
        n = safe_int(s.get("active"))
        seg = f"{dot} {s.get('name')}"
        if typ:
            seg += f" ({typ})"
        seg += f"  {n:,} active stream" + ("" if n == 1 else "s")
        lines.append(seg)
    online = sum(1 for s in servers if s.get("online"))
    return {"ok": True, "status": 200,
            "detail": f"🖥️ Media servers ({online}/{len(servers)} online):\n"
                      + "\n".join(lines)}


async def _violations_skill(host_row: dict, chip: dict, *,
                            host_id: Optional[str] = None) -> dict:
    """Read-only: the most recent account-sharing violations from
    ``GET /violations``. Never raises."""
    api_key, base, err = _resolve_target(host_row, chip)
    if err:
        return err
    print(f"[tracearr] INFO tracearr_violations host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            data = await _call(cli, base, api_key, "violations", pageSize=15)
    except RuntimeError as e:
        return {"ok": False, "status": 0, "detail": str(e)}
    rows = data.get("data") if isinstance(data, dict) else None
    viols = rows if isinstance(rows, list) else []
    lines = []
    for v in viols[:15]:
        if not isinstance(v, dict):
            continue
        sev = str(v.get("severity") or "").strip().lower()
        emoji = _SEVERITY_EMOJI.get(sev, "⚠️")
        user_obj = as_dict(v.get("user"))
        user = str(user_obj.get("username") or "?").strip()
        rule_obj = as_dict(v.get("rule"))
        rule = str(rule_obj.get("name") or rule_obj.get("type") or "violation").strip()
        server = str(v.get("serverName") or "").strip()
        seg = f"{emoji} {user} — {rule}"
        if server:
            seg += f" ({server})"
        lines.append(seg)
    if not lines:
        return {"ok": True, "status": 200, "detail": "✅ No recent violations."}
    total = safe_int((data.get("meta") or {}).get("total")) if isinstance(data, dict) else 0
    header = f"🚨 Recent violations ({total or len(lines):,}):"
    return {"ok": True, "status": 200, "detail": header + "\n" + "\n".join(lines)}
