"""FlareSolverr per-app module (Cloudflare-challenge solver proxy).

Wires a FlareSolverr instance into the OmniGrid Apps surface following the
per-app contract (no-auth ``netboot.xyz`` / ``ddns_updater`` shape):

    SLUGS               — catalog slugs this module handles ("flaresolverr").
    requires_api_key()  — False. FlareSolverr has NO authentication; the editor
                          only needs the instance URL (its API root, default
                          port 8191) + a cache TTL.
    test_credential(host_row, chip, candidate_key, *, payload) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None    (AI context)
    SKILLS / run_skill  — status (read) + sessions (read, rich list with a
                          per-row destroy action) + destroy-session (write;
                          DESTRUCTIVE, arg) + destroy-all (write; DESTRUCTIVE,
                          no-arg — clears every open session).

What this is
-----------
FlareSolverr is a proxy server that solves Cloudflare / DDoS-Guard browser
challenges so the *arr indexers (Prowlarr / Jackett / FlareSolverr-tagged
Torznab) can scrape protected trackers. It runs a headless browser and exposes
a tiny JSON API:

    GET  /                      — health: {"msg": "FlareSolverr is ready!",
                                   "version": "x.y.z", "userAgent": "Mozilla/…"}
    POST /v1 {cmd: sessions.list}    — {"status":"ok","sessions":[id,…],"version":…}
    POST /v1 {cmd: sessions.destroy, session: <id>}  — kill one browser session

So the card answers "is FlareSolverr up, what version, and how many browser
sessions are open" at a glance. The session count matters: each open session
holds a headless-browser instance, and too many slow the host down — the
operator wants to spot + destroy stale sessions.

AI / Telegram skills
--------------------
* ``flaresolverr_status``          — ready + version + session count + user-agent.
* ``flaresolverr_sessions``        — list active session IDs (rich rows, each
                                     with a destroy button).
* ``flaresolverr_destroy_session`` — destroy ONE session by id (DESTRUCTIVE).
* ``flaresolverr_destroy_all``      — clear EVERY open session (DESTRUCTIVE,
                                     no-arg; loops sessions.destroy over the
                                     live list — the "kill all leaked sessions"
                                     button).

Single-instance app (NOT fleet) — one card per pinned chip.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, fetch_gate, peek_cache, resolve_base_url, resolve_cache_ttl)
from logic.coerce import as_dict, as_list, safe_int


def _track_session_ages(host_id: str, service_idx: int, ids: "list[str]",
                        now: int) -> "tuple[int, str]":
    """Track per-session first-seen so the card can show the OLDEST still-open
    session's age (FlareSolverr's sessions.list returns bare ids with no
    timestamp). UPSERTs the live ids into ``flaresolverr_sessions`` (recording
    first_seen on a NEW id, last_seen on every probe — never resetting first_seen),
    prunes rows for sessions no longer present, and returns ``(oldest_age_s,
    oldest_id)``. ``(0, "")`` when there are no sessions or on any DB error (a
    tracking hiccup must never fail the card). Sync — call via asyncio.to_thread."""
    from logic.db import db_conn  # noqa: PLC0415
    if not host_id:
        return 0, ""
    try:
        with db_conn() as c:
            if not ids:
                # No open sessions → clear this chip's tracking so a destroyed
                # session doesn't linger as a phantom "oldest".
                c.execute("DELETE FROM flaresolverr_sessions WHERE host_id=? AND "
                          "service_idx=?", (host_id, int(service_idx)))
                return 0, ""
            for sid in ids:
                c.execute(
                    "INSERT INTO flaresolverr_sessions "
                    "(host_id, service_idx, session_id, first_seen_ts, last_seen_ts) "
                    "VALUES (?,?,?,?,?) "
                    "ON CONFLICT(host_id, service_idx, session_id) "
                    "DO UPDATE SET last_seen_ts=excluded.last_seen_ts",
                    (host_id, int(service_idx), sid, now, now))
            placeholders = ",".join("?" * len(ids))
            c.execute(
                f"DELETE FROM flaresolverr_sessions WHERE host_id=? AND "
                f"service_idx=? AND session_id NOT IN ({placeholders})",
                (host_id, int(service_idx), *ids))
            row = c.execute(
                "SELECT session_id, first_seen_ts FROM flaresolverr_sessions "
                "WHERE host_id=? AND service_idx=? ORDER BY first_seen_ts ASC LIMIT 1",
                (host_id, int(service_idx))).fetchone()
            if row:
                return max(0, now - safe_int(row["first_seen_ts"])), str(row["session_id"])
    except Exception as e:  # noqa: BLE001
        print(f"[flaresolverr] session-age tracking skipped: {type(e).__name__}: {e}")
    return 0, ""


def _fmt_age(seconds: int) -> str:
    """Humanise a session age in seconds → ``Nm`` / ``Nh Mm`` / ``Nd Mh``. ``""``
    for non-positive."""
    s = max(0, int(seconds))
    if s <= 0:
        return ""
    days, rem = divmod(s, 86400)
    hrs, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return f"{days}d {hrs}h" if hrs else f"{days}d"
    if hrs:
        return f"{hrs}h {mins}m" if mins else f"{hrs}h"
    return f"{mins}m" if mins else "<1m"

# Catalog template slug handled by this module.
SLUGS: tuple[str, ...] = ("flaresolverr",)

DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}

# Cap on rich-item rows a list skill returns.
_MAX_ROWS = 50

SKILLS: tuple[dict, ...] = (
    {
        "id": "flaresolverr_status",
        "name": "FlareSolverr status",
        "ai_phrases": ("flaresolverr status, is flaresolverr up, cloudflare "
                       "solver status, how many flaresolverr sessions, "
                       "flaresolverr version, flare solver health, is the "
                       "cloudflare bypass working"),
        "destructive": False,
    },
    {
        "id": "flaresolverr_sessions",
        "name": "List FlareSolverr sessions",
        "ai_phrases": ("list flaresolverr sessions, show active sessions, how "
                       "many browser sessions, flaresolverr open sessions, "
                       "what sessions are running"),
        "destructive": False,
    },
    {
        "id": "flaresolverr_destroy_session",
        "name": "Destroy a FlareSolverr session",
        "ai_phrases": ("destroy a flaresolverr session, close session <id>, "
                       "kill the <id> session, remove a flaresolverr session, "
                       "shut down session <id>"),
        "arg": True,
        "arg_hint": "the FlareSolverr session id to destroy",
        "destructive": True,
    },
    {
        "id": "flaresolverr_destroy_all",
        "name": "Clear all FlareSolverr sessions",
        "ai_phrases": ("clear all flaresolverr sessions, destroy every session, "
                       "kill all browser sessions, close all flaresolverr "
                       "sessions, purge flaresolverr sessions, clear leaked "
                       "sessions, reset flaresolverr sessions, wipe all sessions"),
        # DESTRUCTIVE: kills EVERY open browser session (no-arg → one-click
        # "clear them all" when sessions have leaked). Confirm-gated.
        "destructive": True,
    },
)


def requires_api_key() -> bool:
    """False — FlareSolverr has NO authentication; the editor only needs the
    instance URL (its API root) + a cache TTL."""
    return False


async def _post_v1(cli: "httpx.AsyncClient", base: str, payload: dict) -> Any:
    """POST a command to ``/v1`` and return parsed JSON, or None on any non-2xx
    / parse failure (caller decides how to degrade)."""
    try:
        r = await cli.post(base + "/v1", json=payload,
                           headers={"Content-Type": "application/json",
                                    "Accept": "application/json"})
    except (httpx.HTTPError, OSError):
        return None
    if not (200 <= r.status_code < 300):
        return None
    try:
        return r.json()
    except (ValueError, TypeError):
        return None


async def _probe_root(cli: "httpx.AsyncClient", base: str) -> dict:
    """GET ``/`` for the health payload — ``{ready, version, user_agent}``.
    Defensive: returns ``{}`` on any failure (the card degrades to whatever the
    sessions probe gave)."""
    try:
        r = await cli.get(base + "/")
    except (httpx.HTTPError, OSError):
        return {}
    if not (200 <= r.status_code < 400):
        return {}
    try:
        body = as_dict(r.json())
    except (ValueError, TypeError):
        body = {}
    msg = str(body.get("msg") or "").strip()
    return {
        "ready": "ready" in msg.lower(),
        "version": str(body.get("version") or "").strip(),
        "user_agent": str(body.get("userAgent") or "").strip(),
    }


async def _list_sessions(cli: "httpx.AsyncClient", base: str) -> "Optional[list]":
    """Active session ids via ``POST /v1 {cmd: sessions.list}``. ``None`` when
    the call failed (so the card can tell "0 sessions" from "couldn't ask")."""
    body = await _post_v1(cli, base, {"cmd": "sessions.list"})
    if not isinstance(body, dict):
        return None
    return [str(s) for s in as_list(body.get("sessions")) if s]


# noinspection PyUnusedLocal
async def test_credential(host_row: dict, chip: dict, candidate_key: str, *,
                          payload: Optional[dict] = None, **_kw) -> dict:
    """Probe FlareSolverr's health (``GET /``). No auth — ``candidate_key`` /
    ``payload`` are part of the generic route contract but unused. Returns
    ``{ok, detail, status}``."""
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "detail": "no upstream URL configured", "status": 0}
    try:
        async with httpx.AsyncClient(verify=False, timeout=12.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/")
            if not (200 <= r.status_code < 400):
                return {"ok": False, "detail": f"HTTP {r.status_code}",
                        "status": r.status_code}
            root = await _probe_root(cli, base)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    ver = root.get("version") or ""
    detail = f"OK (FlareSolverr {ver})" if ver else "OK (reachable)"
    return {"ok": True, "detail": detail, "status": 200}


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Probe FlareSolverr for the expanded card: GET / for ready/version/UA +
    POST /v1 sessions.list for the active session count. Returns
    ``{available, ready, version, user_agent, sessions, session_ids,
    fetched_at}``. Raises ``ValueError`` (base URL won't resolve) /
    ``RuntimeError`` (upstream error on the health GET — the sessions probe is
    best-effort on top)."""
    now = time.time()
    # No-auth app — credential=True so the gate never raises on a missing
    # secret (it folds the URL-resolve + cache-miss-log shape shared with the
    # other fetch_data openers).
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=True, log_tag="flaresolverr")
    if hit is not None:
        return hit
    url = base + "/"
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url)
            if not (200 <= r.status_code < 400):
                print(f"[flaresolverr] error: fetch host={host_id} url={url} returned "
                      f"HTTP {r.status_code} (check the chip URL points at the "
                      f"FlareSolverr API root, e.g. http://host:8191)")
                raise RuntimeError(f"upstream returned HTTP {r.status_code} for {url}")
            root = await _probe_root(cli, base)
            sessions = await _list_sessions(cli, base)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[flaresolverr] error: fetch host={host_id} url={url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    ids = sessions if isinstance(sessions, list) else []
    # Oldest still-open session's age (leaked / stale-session detection) — from
    # OmniGrid's own first-seen tracking (the API gives no session timestamps).
    # Best-effort; runs off the event loop so the DB touch can't stall the card.
    oldest_age_s, oldest_id = await asyncio.to_thread(
        _track_session_ages, str(host_id), int(service_idx), ids, int(now))
    out: dict = {
        "available": True,
        "ready": bool(root.get("ready")),
        "version": root.get("version") or "",
        "user_agent": root.get("user_agent") or "",
        "sessions": len(ids),
        "session_ids": ids,
        "oldest_session_age_s": oldest_age_s,
        "oldest_session_id": oldest_id,
        "fetched_at": int(now),
    }
    # 30-day open-session usage trend from the lifespan sampler (FlareSolverr
    # has no historical / request-volume API, so this is the only usage signal).
    # Best-effort — never block the card on a sampler-table read.
    try:
        from logic.apps import flaresolverr_sampler as _fs_sampler  # noqa: PLC0415
        from logic.tuning import Tunable as _Tunable  # noqa: PLC0415
        from logic.tuning import tuning_int as _tuning_int  # noqa: PLC0415
        out["usage"] = _fs_sampler.usage_summary(
            str(host_id), int(service_idx),
            days=_tuning_int(_Tunable.FLARESOLVERR_HISTORY_DAYS))
    except Exception as e:  # noqa: BLE001
        print(f"[flaresolverr] usage_summary skipped: {type(e).__name__}: {e}")
    print(f"[flaresolverr] INFO fetched host={host_id} ready={out['ready']} "
          f"ver={out['version'] or '-'} sessions={out['sessions']} "
          f"oldest={oldest_age_s}s")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "ready": bool(data.get("ready")),
        "version": data.get("version") or "",
        "sessions": safe_int(data.get("sessions")),
        "oldest_session_age_s": safe_int(data.get("oldest_session_age_s")),
        "fetched_at": safe_int(data.get("fetched_at")),
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None,
                    arg: Optional[str] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Returns ``{ok, detail, status?}``.
    Raises ValueError on an unknown skill id (route maps to HTTP 404)."""
    if skill_id == "flaresolverr_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "flaresolverr_sessions":
        return await _sessions_skill(host_row, chip, host_id=host_id,
                                     service_idx=service_idx)
    if skill_id == "flaresolverr_destroy_session":
        return await _destroy_session_skill(host_row, chip, arg=arg, host_id=host_id)
    if skill_id == "flaresolverr_destroy_all":
        return await _destroy_all_skill(host_row, chip, host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


# noinspection DuplicatedCode
async def _live_fetch(host_row: dict, chip: dict, *,
                      host_id: Optional[str],
                      service_idx: Optional[int]) -> "tuple[Optional[dict], Optional[dict]]":
    """Force a live ``fetch_data`` for a skill. Returns ``(data, None)`` on
    success or ``(None, error_dict)`` when the fetch raises. Never raises."""
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        return None, {"ok": False, "detail": str(e), "status": 0}
    return data, None


async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch the health + session summary. Never raises."""
    print(f"[flaresolverr] INFO flaresolverr_status host={host_id} "
          f"svc_idx={service_idx} (live fetch)")
    data, ferr = await _live_fetch(host_row, chip, host_id=host_id, service_idx=service_idx)
    if ferr:
        return ferr
    assert data is not None
    ver = str(data.get("version") or "").strip()
    sessions = safe_int(data.get("sessions"))
    ua = str(data.get("user_agent") or "").strip()
    head = "🛡️ FlareSolverr is up and ready" if data.get("ready") else "🛡️ FlareSolverr is up"
    lines = [
        head + (f" — v{ver}" if ver else ""),
        f"🧩 {sessions} active session(s)",
    ]
    oldest = safe_int(data.get("oldest_session_age_s"))
    if sessions and oldest > 0:
        lines.append(f"⏳ Oldest session open {_fmt_age(oldest)}")
    if ua:
        lines.append(f"🌐 UA: {ua}")
    _usage = data.get("usage")
    usage = _usage if isinstance(_usage, dict) else {}
    peak = safe_int(usage.get("peak"))
    active_days = safe_int(usage.get("active_days"))
    if usage.get("samples"):
        lines.append(f"📈 {usage.get('days', 30)}d usage: peak {peak} · "
                     f"avg {usage.get('avg', 0)} · active {active_days} day(s)")
    return {"ok": True, "status": 200, "detail": "\n".join(lines),
            "ready": bool(data.get("ready")), "version": ver, "sessions": sessions,
            "oldest_session_age_s": safe_int(data.get("oldest_session_age_s")),
            "usage": usage}


async def _sessions_skill(host_row: dict, chip: dict, *,
                          host_id: Optional[str] = None,
                          service_idx: Optional[int] = None) -> dict:
    """Read-only: list active browser sessions as rich rows, each with a
    per-row destroy action. Never raises."""
    print(f"[flaresolverr] INFO flaresolverr_sessions host={host_id} (live fetch)")
    data, ferr = await _live_fetch(host_row, chip, host_id=host_id, service_idx=service_idx)
    if ferr:
        return ferr
    assert data is not None
    ids = [str(s) for s in as_list(data.get("session_ids")) if s]
    if not ids:
        return {"ok": True, "status": 200, "detail": "🧩 No active FlareSolverr sessions."}
    items: list = []
    lines: list = []
    for sid in ids[:_MAX_ROWS]:
        items.append({
            "title": sid,
            "subtitle": "browser session",
            "row_action": {
                "skill_id": "flaresolverr_destroy_session",
                "arg": sid,
                "destructive": True,
                "confirm_i18n": "apps.flaresolverr.confirm_destroy",
            },
        })
        lines.append(f"• {sid}")
    return {
        "ok": True,
        "status": 200,
        "detail": "🧩 Active FlareSolverr sessions:\n" + "\n".join(lines),
        "items": items,
        "count": len(items),
        "count_i18n": "apps.flaresolverr.sessions_count",
    }


async def _destroy_session_skill(host_row: dict, chip: dict, *,
                                 arg: Optional[str],
                                 host_id: Optional[str] = None) -> dict:
    """DESTRUCTIVE: destroy ONE browser session by id via ``POST /v1
    {cmd: sessions.destroy}``. Never raises."""
    sid = (arg or "").strip()
    if not sid:
        return {"ok": False, "status": 0,
                "detail": "no session id given (run \"list FlareSolverr sessions\" first)"}
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    print(f"[flaresolverr] INFO flaresolverr_destroy_session host={host_id} session={sid!r}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            body = await _post_v1(cli, base, {"cmd": "sessions.destroy", "session": sid})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"destroy failed: {type(e).__name__}: {e}"}
    status = str(as_dict(body).get("status") or "").strip().lower()
    if body is None:
        return {"ok": False, "status": 502,
                "detail": f"FlareSolverr didn't accept the destroy for \"{sid}\""}
    if status and status != "ok":
        return {"ok": False, "status": 400,
                "detail": str(as_dict(body).get("message") or f"could not destroy \"{sid}\"")}
    return {"ok": True, "status": 200,
            "detail": f"🧩 Destroyed FlareSolverr session \"{sid}\"."}


async def _destroy_all_skill(host_row: dict, chip: dict, *,
                             host_id: Optional[str] = None) -> dict:
    """DESTRUCTIVE: clear EVERY open browser session — read the live
    ``sessions.list`` then loop ``sessions.destroy`` over each id. The one-click
    "kill all leaked sessions" an operator reaches for when sessions have piled
    up. Reports how many were destroyed + any that failed (honest partial
    result). Destroys SEQUENTIALLY — each session is a real browser instance, so
    a parallel teardown could race the solver. Never raises."""
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    print(f"[flaresolverr] INFO flaresolverr_destroy_all host={host_id} (live fetch)")
    try:
        async with httpx.AsyncClient(verify=False, timeout=30.0,
                                     follow_redirects=True) as cli:
            ids = await _list_sessions(cli, base)
            if ids is None:
                return {"ok": False, "status": 502,
                        "detail": "couldn't read the FlareSolverr session list to clear"}
            ids = [str(s) for s in ids if str(s).strip()]
            if not ids:
                return {"ok": True, "status": 200,
                        "detail": "🧩 No active FlareSolverr sessions — nothing to clear."}
            destroyed = 0
            failed: list = []
            for sid in ids:
                try:
                    body = await _post_v1(cli, base, {"cmd": "sessions.destroy",
                                                      "session": sid})
                except (httpx.HTTPError, OSError):
                    failed.append(sid)
                    continue
                st = str(as_dict(body).get("status") or "").strip().lower()
                if body is not None and (not st or st == "ok"):
                    destroyed += 1
                else:
                    failed.append(sid)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0,
                "detail": f"clear failed: {type(e).__name__}: {e}"}
    if destroyed and not failed:
        return {"ok": True, "status": 200, "removed": destroyed,
                "detail": f"🧩 Cleared all {destroyed} FlareSolverr session(s)."}
    if destroyed and failed:
        shown = ", ".join(failed[:5]) + ("…" if len(failed) > 5 else "")
        return {"ok": True, "status": 200, "removed": destroyed,
                "detail": f"🧩 Cleared {destroyed} session(s); {len(failed)} could "
                          f"not be destroyed ({shown})."}
    return {"ok": False, "status": 502,
            "detail": f"couldn't destroy any of the {len(ids)} session(s)"}
