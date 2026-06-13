"""Pi-hole (v6 FTL) per-app module.

Mirrors the AdGuard Home fleet app (``logic/apps/adguardhome.py``):
2+ Pi-holes aggregate into ONE card (app-level extras) and the skills
are FLEET-wide (enable / disable / refresh apply to every Pi-hole at
once). Public surface matches the per-app contract documented in
``logic/apps/registry.py``:

    SLUGS               — catalog slugs this module handles.
    requires_api_key()  — True (the chip's ``api_key`` field stores the
                          Pi-hole *password*; Pi-hole v6 has NO username).
    resolve_base_url(host_row, chip) -> str
    test_credential(host_row, chip, candidate_key, **_kw) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None
    SKILLS / FLEET_SKILLS / run_skill(...) — fleet-wide actions.

Auth model
----------
Pi-hole v6's FTL REST API is SESSION based, password-only (no username):
``POST /api/auth {"password": "<app password>"}`` returns a session
``sid`` (+ ``validity`` seconds). Subsequent requests carry the SID in
the ``X-FTL-SID`` header. The password is the secret and lives in the
chip's ``api_key`` field (so the keep-current / ``_set`` / test-credential
plumbing applies unchanged); there is NO ``username`` chip field (unlike
AdGuard). Pi-hole caps concurrent sessions, so we CACHE the SID per host
and reuse it until ~expiry instead of authenticating per request.

Endpoints used (Pi-hole v6 FTL API):
    POST   /api/auth                       — {password} -> {session:{sid,validity}}
    DELETE /api/auth                       — logout (best-effort, X-FTL-SID)
    GET    /api/stats/summary              — queries / blocked / clients / gravity
    GET    /api/dns/blocking               — {blocking: enabled|disabled, timer}
    POST   /api/dns/blocking               — {blocking: bool, timer: sec|null}
    GET    /api/stats/top_domains?blocked=true — top blocked domain
    GET    /api/info/version               — core version string (best-effort)
    POST   /api/action/gravity             — run gravity update (refresh blocklists)

API reference: https://ftl.pi-hole.net/master/docs/
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, fetch_gate, fleet_blocker_action, fleet_blocker_status,
    fleet_disable_skills, fleet_fan_out, fleet_instances, fleet_run_skill,
    fleet_top, fmt_int_grouped, peek_cache, resolve_base_url, resolve_cache_ttl)
from logic.coerce import safe_float, safe_int

# Catalog template slugs handled by this module. The catalog ships
# ``pihole``; the aliases catch operator-edited chips that kept the brand
# but dropped the catalog link.
SLUGS: tuple[str, ...] = ("pihole", "pi-hole", "pihole-v6")

# Fleet-wide SKILLS — run_skill ignores the targeted chip and fans out to
# EVERY Pi-hole instance. ``pihole_status`` is read-only.
SKILLS: tuple[dict, ...] = (
    {
        "id": "pihole_status",
        "name": "Show Pi-hole status",
        "ai_phrases": ("pihole status, pi-hole stats, dns blocking stats, "
                       "how many queries blocked today, blocked percentage, "
                       "show pihole, ad blocking summary"),
        "destructive": False,
    },
    {
        "id": "pihole_enable",
        "name": "Enable blocking",
        "ai_phrases": ("enable pihole, turn on blocking, enable dns blocking, "
                       "turn pihole on, start blocking ads"),
        "destructive": False,
    },
    {
        "id": "pihole_disable",
        "name": "Disable blocking",
        "ai_phrases": ("disable pihole, turn off blocking, pause ad blocking, "
                       "stop blocking, turn pihole off indefinitely"),
        "destructive": True,
    },
    *fleet_disable_skills("pihole", "pihole"),
    {
        "id": "pihole_refresh",
        "name": "Update gravity (blocklists)",
        "ai_phrases": ("update pihole gravity, refresh blocklists, update filter "
                       "lists, refresh dns filters, run gravity"),
        "destructive": False,
    },
    {
        "id": "pihole_reenable",
        "name": "Re-enable (cancel timed disable)",
        "ai_phrases": ("cancel timed disable, re-enable pihole now, "
                       "turn blocking back on, undo disable"),
        "destructive": False,
    },
    {
        "id": "pihole_block_domain",
        "name": "Block a domain (fleet)",
        "ai_phrases": ("block <domain> on pihole, block a domain everywhere, "
                       "add to the denylist, block <domain> on all pi-holes, "
                       "blacklist <domain>"),
        "arg": True,
        "arg_hint": "the domain to block on every Pi-hole host",
        "destructive": True,
    },
    {
        "id": "pihole_unblock_domain",
        "name": "Unblock a domain (fleet)",
        "ai_phrases": ("unblock <domain> on pihole, remove from the denylist, "
                       "allow <domain> everywhere, unblock <domain> on all "
                       "pi-holes, whitelist <domain>"),
        "arg": True,
        "arg_hint": "the domain to unblock on every Pi-hole host",
        "destructive": False,
    },
    {
        "id": "pihole_block_regex",
        "name": "Add a regex block (fleet)",
        "ai_phrases": ("block a regex on pihole, add a regex denylist rule, block "
                       "a wildcard domain, block everything matching <pattern>, "
                       "add a regex block <pattern>, block all <brand> subdomains"),
        "arg": True,
        "arg_hint": "the regex / wildcard pattern to block on every Pi-hole host (e.g. (^|\\.)ads\\..*)",
        "destructive": True,
    },
)

# Module-wide fleet flag — the registry stamps ``fleet: True`` onto each
# skill so the Telegram slash command + AI dispatch run host-less.
FLEET_SKILLS: bool = True

# Per-app modules intentionally share the cache + requires_api_key +
# resolve_base_url shape (PyCharm flags the duplicate Info-level; not
# suppressible). 30s data cache mirrors AdGuard.
# Per-instance data-cache TTL DEFAULT — overridable per chip via the
# editor's `cache_ttl` field (resolve_cache_ttl); NOT a global TUNABLE.
DEFAULT_CACHE_TTL_S = 30
_data_cache: dict[str, tuple[float, dict]] = {}

# Session-ID cache: resolved-base-URL -> (sid, expires_at). Pi-hole caps
# concurrent sessions, so we reuse a SID until ~expiry instead of
# authenticating on every fetch.
_sid_cache: dict[str, tuple[str, float]] = {}


def requires_api_key() -> bool:
    """True — the chip's ``api_key`` field carries the Pi-hole password
    (v6 has no username); the editor renders a password input + Test."""
    return True


def _password(chip: dict, *, candidate: Optional[str] = None) -> str:
    """Resolve the Pi-hole password for a chip. An explicit ``candidate``
    (pre-save test) wins; else the stored ``api_key``."""
    return (candidate if candidate is not None else "").strip() or (chip.get("api_key") or "").strip()


def _sid_key(base: str) -> str:
    """Cache-key for the per-process SID cache — the instance's resolved base
    URL is its identity. One Pi-hole server has a single session password, so
    the URL alone disambiguates instances; the password is deliberately NOT
    part of the key (no secret material lands in a dict key, and no hashing of
    sensitive data is needed). A password rotation is handled by the 401/403
    re-auth retry in ``fetch_data`` — the stale SID is popped and we
    re-authenticate with the new password."""
    return base


async def _authenticate(base: str, password: str) -> str:
    """POST /api/auth and return a fresh SID. Raises RuntimeError on
    rejected / unreachable auth."""
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0, follow_redirects=True) as cli:
            r = await cli.post(base + "/api/auth", json={"password": password})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        raise RuntimeError(f"{type(e).__name__}: {e}")
    if r.status_code in (401, 403):
        raise RuntimeError("auth failed (check password)")
    if r.status_code != 200:
        raise RuntimeError(f"auth HTTP {r.status_code}")
    try:
        sess = (r.json() or {}).get("session") or {}
    except (ValueError, TypeError):  # noqa: BLE001
        raise RuntimeError("auth returned non-JSON")
    sid = str(sess.get("sid") or "").strip()
    if not sess.get("valid") or not sid:
        raise RuntimeError("auth rejected (check password)")
    validity = safe_int(sess.get("validity")) or 1800
    _sid_cache[_sid_key(base)] = (sid, time.time() + max(60, validity - 60))
    return sid


async def _get_sid(base: str, password: str) -> str:
    """Return a valid SID — reuse the cached one until ~expiry, else
    authenticate. Raises RuntimeError on auth failure."""
    cached = _sid_cache.get(_sid_key(base))
    if cached and cached[1] > time.time():
        return cached[0]
    return await _authenticate(base, password)


async def _logout(base: str, sid: str) -> None:
    """Best-effort DELETE /api/auth — frees the session slot. Never raises."""
    # noinspection PyBroadException
    try:
        async with httpx.AsyncClient(verify=False, timeout=6.0, follow_redirects=True) as cli:
            await cli.request("DELETE", base + "/api/auth", headers={"X-FTL-SID": sid})
    except Exception:  # noqa: BLE001
        pass


async def test_credential(host_row: dict, chip: dict, candidate_key: str, **_kw) -> dict:
    """Probe POST /api/auth with the candidate password. ``candidate_key``
    is the password (Pi-hole v6 has no username). Returns
    ``{ok, detail, status}``. Logs out the test session so it doesn't
    occupy a session slot. Pi-hole is single-secret, so the generic
    route's ``payload`` kwarg (multi-field creds) is ignored via
    ``**_kw`` per the per-app contract."""
    password = _password(chip, candidate=(candidate_key or "").strip() or None)
    if not password:
        return {"ok": False, "detail": "password required", "status": 0}
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "detail": "no upstream URL configured", "status": 0}
    try:
        sid = await _authenticate(base, password)
    except RuntimeError as e:
        print(f"[pihole] warning: test-connection {base}/api/auth failed -- {e}")
        # Surface the auth failure verbatim; map the common cases to a
        # clean status for the SPA.
        msg = str(e)
        status = 401 if "auth failed" in msg or "rejected" in msg else 0
        return {"ok": False, "detail": msg, "status": status}
    # Optionally read the version for a friendlier OK detail (best-effort).
    ver = ""
    # noinspection PyBroadException
    try:
        async with httpx.AsyncClient(verify=False, timeout=8.0, follow_redirects=True) as cli:
            vr = await cli.get(base + "/api/info/version", headers={"X-FTL-SID": sid})
        if vr.status_code == 200:
            ver = _version_str(vr.json())
    except Exception:  # noqa: BLE001
        pass
    print(f"[pihole] INFO test-connection url={base} -> OK (sid acquired){(' v' + ver) if ver else ''}")
    await _logout(base, sid)
    return {"ok": True, "detail": f"OK{(' -- ' + ver) if ver else ''}", "status": 200}


def _version_str(info: Any) -> str:
    """Extract a core version string from GET /api/info/version's nested
    shape (``{version:{core:{local:{version}}}}``). Best-effort."""
    if not isinstance(info, dict):
        return ""
    _ver = info.get("version")
    ver = _ver if isinstance(_ver, dict) else info
    core = (ver or {}).get("core") if isinstance(ver, dict) else None
    if isinstance(core, dict):
        _local = core.get("local")
        local = _local if isinstance(_local, dict) else core
        v = str((local or {}).get("version") or "").strip()
        if v:
            return v
    return ""


def _top_domain(body: Any) -> Optional[dict]:
    """First ``{domain, count}`` row from a /api/stats/top_domains response
    → ``{name, count}`` or None."""
    if not isinstance(body, dict):
        return None
    domains = body.get("domains")
    if isinstance(domains, list) and domains and isinstance(domains[0], dict):
        name = str(domains[0].get("domain") or "").strip()
        if name:
            return {"name": name, "count": safe_int(domains[0].get("count"))}
    return None


def _top_domain_list(body: Any, cap: int = 10) -> list:
    """Full top-N ``[{name, count}]`` (highest-first, capped) from a
    /api/stats/top_domains response — P2 distribution bars. [] when absent."""
    out: list = []
    if not isinstance(body, dict):
        return out
    domains = body.get("domains")
    if isinstance(domains, list):
        for d in domains:
            if isinstance(d, dict):
                name = str(d.get("domain") or "").strip()
                if name:
                    out.append({"name": name, "count": safe_int(d.get("count"))})
    out.sort(key=lambda r: r["count"], reverse=True)
    return out[:cap]


def _upstreams_shape(body: Any) -> dict:
    """Parse /api/stats/upstreams (Pi-hole v6) into ``{cache_pct, forwarded_pct,
    top_upstream}`` — P1 cache-vs-forwarded split. The ``upstreams`` list mixes
    the synthetic ``cache`` + ``blocklist`` entries with the real forwarding
    resolvers; cache_pct = cache / total, top_upstream = the busiest real
    resolver. Zeroed shape when absent."""
    out: dict = {"cache_pct": 0.0, "forwarded_pct": 0.0, "top_upstream": None}
    ups = body.get("upstreams") if isinstance(body, dict) else None
    if not isinstance(ups, list):
        return out
    total = 0
    cache = 0
    forwarded = 0
    top = None
    top_count = -1
    for u in ups:
        if not isinstance(u, dict):
            continue
        cnt = safe_int(u.get("count"))
        total += cnt
        nm = str(u.get("name") or "").strip().lower()
        if nm == "cache":
            cache += cnt
        elif nm == "blocklist":
            continue  # blocked locally, not a forwarding upstream
        else:
            forwarded += cnt
            label = str(u.get("name") or "").strip() or str(u.get("ip") or "").strip()
            if label and cnt > top_count:
                top_count = cnt
                top = {"name": label, "count": cnt}
    if total > 0:
        out["cache_pct"] = round(cache / total * 100.0, 1)
        out["forwarded_pct"] = round(forwarded / total * 100.0, 1)
    out["top_upstream"] = top
    return out


def _top_client(body: Any) -> Optional[dict]:
    """First client from /api/stats/top_clients (``{clients:[{name, ip,
    count}]}``) → ``{name, count}`` (name falls back to ip) or None."""
    if not isinstance(body, dict):
        return None
    clients = body.get("clients")
    if isinstance(clients, list) and clients and isinstance(clients[0], dict):
        name = (str(clients[0].get("name") or "").strip()
                or str(clients[0].get("ip") or "").strip())
        if name:
            return {"name": name, "count": safe_int(clients[0].get("count"))}
    return None


def _history_series(body: Any, cap: int = 48) -> "tuple[list, list]":
    """Parse /api/history (``{history:[{timestamp, total, blocked, …}]}``) into
    ``(queries_series, blocked_series)`` — per-bin total + blocked counts, last
    ``cap`` bins, for the queries-vs-blocked card chart. ``([], [])`` when
    absent / malformed."""
    rows = body.get("history") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return [], []
    q, b = [], []
    for r in rows:
        if not isinstance(r, dict):
            continue
        q.append(safe_int(r.get("total")))
        b.append(safe_int(r.get("blocked")))
    return q[-cap:], b[-cap:]


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch ONE Pi-hole host's current stats — same per-host shape the
    SPA aggregates across instances (parallel to AdGuard). Raises
    ``ValueError`` (-> HTTP 400) when the password / URL is missing,
    ``RuntimeError`` (-> HTTP 502) on an upstream failure."""
    password = _password(chip)
    now = time.time()
    base, hit = fetch_gate(host_row, chip, host_id, service_idx,
                           _data_cache, resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=password, log_tag="pihole")
    if hit is not None:
        return hit

    async def _authed_get(auth_sid: str, path: str) -> tuple[int, Any]:
        async with httpx.AsyncClient(verify=False, timeout=12.0, follow_redirects=True) as cli:
            r = await cli.get(base + path, headers={"X-FTL-SID": auth_sid, "Accept": "application/json"})
        # noinspection PyBroadException
        try:
            body: Any = r.json()
        except Exception:  # noqa: BLE001
            body = None
        return r.status_code, body

    try:
        sid = await _get_sid(base, password)
        # One re-auth retry if the cached SID went stale (401).
        sc, summary = await _authed_get(sid, "/api/stats/summary")
        if sc in (401, 403):
            _sid_cache.pop(_sid_key(base), None)
            sid = await _authenticate(base, password)
            sc, summary = await _authed_get(sid, "/api/stats/summary")
        if sc != 200 or not isinstance(summary, dict):
            raise RuntimeError(f"HTTP {sc} for {base}/api/stats/summary")
        # Blocking status + tops + version + history — best-effort in parallel.
        ((_bc, blocking), (_tc, topd), (_qc, topq), (_clc, topcl),
         (_vc, vinfo), (_hc, history), (_uc, upstreams)) = await asyncio.gather(
            _authed_get(sid, "/api/dns/blocking"),
            _authed_get(sid, "/api/stats/top_domains?blocked=true&count=10"),
            _authed_get(sid, "/api/stats/top_domains?blocked=false&count=10"),
            _authed_get(sid, "/api/stats/top_clients?count=1"),
            _authed_get(sid, "/api/info/version"),
            _authed_get(sid, "/api/history"),
            _authed_get(sid, "/api/stats/upstreams"),
        )
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[pihole] error: fetch host={host_id} failed -- {type(e).__name__}: {e}")
        raise RuntimeError(f"{type(e).__name__}: {e}")
    except RuntimeError as e:
        print(f"[pihole] error: fetch host={host_id} failed -- {e}")
        raise

    # Single-var narrowing (assign-then-isinstance) so the type checker
    # sees a clean `dict` on each sub-object — calling `.get()` twice in a
    # ternary leaves the value typed `Any | None` and flags every `.get`.
    _q = summary.get("queries")
    queries_obj = _q if isinstance(_q, dict) else {}
    _cl = summary.get("clients")
    clients_obj = _cl if isinstance(_cl, dict) else {}
    _gr = summary.get("gravity")
    gravity_obj = _gr if isinstance(_gr, dict) else {}

    queries = safe_int(queries_obj.get("total"))
    blocked = safe_int(queries_obj.get("blocked"))
    blocked_pct = round(safe_float(queries_obj.get("percent_blocked")), 2)
    if blocked_pct == 0.0 and queries > 0:
        blocked_pct = round((blocked / queries) * 100.0, 2)
    domains_blocked = safe_int(gravity_obj.get("domains_being_blocked"))
    num_clients = safe_int(clients_obj.get("active"))

    blocking_state = ""
    timer_remaining = 0
    if isinstance(blocking, dict):
        blocking_state = str(blocking.get("blocking") or "").strip().lower()
        timer_remaining = safe_int(blocking.get("timer"))
    protection_enabled = blocking_state in ("", "enabled", "true")

    top_blocked = _top_domain(topd)
    top_queried = _top_domain(topq)
    top_client = _top_client(topcl)
    queries_series, blocked_series = _history_series(history)
    # P2 — full top-blocked / top-permitted distributions for the bar chart.
    top_blocked_list = _top_domain_list(topd)
    top_permitted_list = _top_domain_list(topq)
    # P1 — cache-vs-forwarded split + busiest upstream resolver.
    ups = _upstreams_shape(upstreams)

    out: dict = {
        "ok": True,
        "host": str(host_row.get("label") or host_id),
        "host_id": host_id,
        "protection_enabled": protection_enabled,
        # Seconds until a timed-disable auto-re-enables (0 = none / indefinite).
        "disabled_timer_s": timer_remaining if not protection_enabled else 0,
        "queries_today": queries,
        "blocked_today": blocked,
        "blocked_pct": blocked_pct,
        "blocklist_rules": domains_blocked,
        "num_clients": num_clients,
        "top_blocked_domain": top_blocked,
        "top_queried_domain": top_queried,
        "top_client": top_client,
        # P1 — cache-vs-forwarded query split + busiest upstream resolver.
        "cache_pct": ups["cache_pct"],
        "forwarded_pct": ups["forwarded_pct"],
        "top_upstream": ups["top_upstream"],
        # P2 — top-blocked / top-permitted distribution lists (top 10 each).
        "top_blocked_list": top_blocked_list,
        "top_permitted_list": top_permitted_list,
        # 10-min binned queries-vs-blocked series (from /api/history) for the
        # aggregated card chart — capped to the most recent 48 bins.
        "queries_series": queries_series,
        "blocked_series": blocked_series,
        "version": _version_str(vinfo),
        "fetched_at": int(now),
    }
    # Fleet-wide long-horizon blocked-% trend (same for every host — the SPA
    # aggregate reads it off the first OK host). Best-effort.
    try:
        from logic.apps import pihole_sampler as _ph_sampler  # noqa: PLC0415
        out["fleet_trend"] = _ph_sampler.trend_summary()
    except Exception as e:  # noqa: BLE001
        print(f"[pihole] warning: trend_summary failed: {e}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``. Returns the last fetched per-host stats or None."""
    return peek_cache(_data_cache, host_id, service_idx)


# ---------------------------------------------------------------------------
# Fleet actions (run_skill) — every action skill fans out to EVERY Pi-hole
# instance in hosts_config (the operator's explicit "all the fleet" model).
# ---------------------------------------------------------------------------
def _instances() -> list:
    """Enumerate every Pi-hole instance:
    ``[(host_id, service_idx, host_row, chip)]``. Shared fleet helper —
    deduped, never raises."""
    return fleet_instances(SLUGS)


async def _set_blocking(base: str, sid: str, enabled: bool, timer_s: int = 0) -> None:
    """POST /api/dns/blocking {blocking, timer}. ``timer_s`` > 0 on a
    disable schedules an auto-re-enable. Raises RuntimeError on failure."""
    body: dict[str, Any] = {"blocking": bool(enabled),
                            "timer": (int(timer_s) if (not enabled and timer_s > 0) else None)}
    try:
        async with httpx.AsyncClient(verify=False, timeout=12.0, follow_redirects=True) as cli:
            r = await cli.post(base + "/api/dns/blocking", json=body,
                               headers={"X-FTL-SID": sid})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        raise RuntimeError(f"{type(e).__name__}: {e}")
    if r.status_code in (401, 403):
        raise RuntimeError(f"auth failed: HTTP {r.status_code}")
    if r.status_code not in (200, 201, 204):
        raise RuntimeError(f"HTTP {r.status_code}")


# noinspection DuplicatedCode
async def _run_gravity(base: str, sid: str) -> None:
    """POST /api/action/gravity — runs ``pihole -g`` (gravity / blocklist
    update). The endpoint may stream output; we just need a non-error
    status. Generous timeout (gravity can take a while). Raises on failure."""
    try:
        async with httpx.AsyncClient(verify=False, timeout=120.0, follow_redirects=True) as cli:
            r = await cli.post(base + "/api/action/gravity", headers={"X-FTL-SID": sid})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        raise RuntimeError(f"{type(e).__name__}: {e}")
    if r.status_code in (401, 403):
        raise RuntimeError(f"auth failed: HTTP {r.status_code}")
    if r.status_code not in (200, 201, 204):
        raise RuntimeError(f"HTTP {r.status_code}")


# noinspection DuplicatedCode
def _extra_lines(ok_rows: list, _totals: dict) -> list:
    """Pi-hole-specific extra stat lines for the fleet status: the top queried
    domain + top client across the fleet. The extraction shape is shared with
    AdGuard's _avg_processing_lines by design (fleet-app convention). Passed as
    ``extra_lines_fn`` to ``fleet_blocker_status``."""
    lines = []
    tq = fleet_top(ok_rows, "top_queried_domain")
    if tq and tq.get("name"):
        lines.append(f"🔎 Top queried: {tq.get('name')} ({fmt_int_grouped(tq.get('count'))})")
    tc = fleet_top(ok_rows, "top_client")
    if tc and tc.get("name"):
        lines.append(f"💻 Top client: {tc.get('name')} ({fmt_int_grouped(tc.get('count'))})")
    return lines


async def _skill_status() -> dict:
    """Read-only: live-fetch every instance + aggregate into a formatted
    detail block (web inline + Telegram + AI). The top-queried / top-client
    lines are Pi-hole's extra_lines (it has no avg-processing metric like
    AdGuard). Never raises."""
    return await fleet_blocker_status(
        _instances(), fetch_data, app_label="Pi-hole",
        header="🕳️ Pi-hole", protection_label="Blocking",
        extra_lines_fn=_extra_lines)


# noinspection DuplicatedCode
async def _set_domain(base: str, sid: str, domain: str, block: bool) -> None:
    """Add (block) / remove (unblock) ``domain`` on the host's exact denylist
    via POST /api/domains/deny/exact {domain} / DELETE /api/domains/deny/exact/
    {domain}. Idempotent — a 409 'already exists' (add) and a 404 'not found'
    (remove) are treated as success. Raises RuntimeError otherwise."""
    try:
        async with httpx.AsyncClient(verify=False, timeout=12.0, follow_redirects=True) as cli:
            if block:
                r = await cli.post(base + "/api/domains/deny/exact",
                                   json={"domain": domain},
                                   headers={"X-FTL-SID": sid})
            else:
                r = await cli.request("DELETE",
                                      base + "/api/domains/deny/exact/" + domain,
                                      headers={"X-FTL-SID": sid})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        raise RuntimeError(f"{type(e).__name__}: {e}")
    if r.status_code in (401, 403):
        raise RuntimeError(f"auth failed: HTTP {r.status_code}")
    # 409 = already on the list (add); 404 = wasn't on the list (remove) — both
    # are the requested end-state, so treat as success.
    if r.status_code in (200, 201, 204) or (block and r.status_code == 409) \
        or (not block and r.status_code == 404):
        return
    raise RuntimeError(f"HTTP {r.status_code}")


async def _skill_fleet_action(action: str, seconds: int = 0) -> dict:
    """Apply enable / disable / refresh across EVERY instance (the shared
    fan-out shell lives in ``_common.fleet_fan_out``; only the per-host
    ``_one`` closure is app-specific). ``seconds`` is the timed-disable
    window — Pi-hole's API takes seconds natively."""

    async def _one(hid, _sidx, hrow, chip):
        password = _password(chip)
        base = resolve_base_url(hrow, chip)
        if not (password and base):
            return hid, False, "no creds / url"
        try:
            sid = await _get_sid(base, password)
            if action == "enable":
                await _set_blocking(base, sid, True)
            elif action == "disable":
                await _set_blocking(base, sid, False, seconds)
            elif action == "refresh":
                await _run_gravity(base, sid)
            else:
                return hid, False, f"unknown action {action}"
        except RuntimeError as e:
            # A stale cached SID surfaces as "auth failed" — drop + one retry.
            if "auth failed" in str(e):
                _sid_cache.pop(_sid_key(resolve_base_url(hrow, chip)), None)
            return hid, False, str(e)
        return hid, True, ""

    return await fleet_blocker_action(_instances(), _one, action=action,
                                      seconds=seconds, app_label="Pi-hole",
                                      log_tag="pihole",
                                      refresh_verb="gravity updated")


async def _skill_set_domain(domain: str, block: bool) -> dict:
    """Block / unblock ``domain`` on EVERY Pi-hole instance (fleet write)."""
    needle = (domain or "").strip().lower()
    if not needle:
        verb = "block" if block else "unblock"
        return {"ok": False, "status": 0,
                "detail": f"no domain given (say e.g. \"{verb} ads.example.com\")"}

    # noinspection DuplicatedCode
    async def _one(hid, _sidx, hrow, chip):
        password = _password(chip)
        base = resolve_base_url(hrow, chip)
        if not (password and base):
            return hid, False, "no creds / url"
        try:
            sid = await _get_sid(base, password)
            await _set_domain(base, sid, needle, block)
        except RuntimeError as e:
            if "auth failed" in str(e):
                _sid_cache.pop(_sid_key(resolve_base_url(hrow, chip)), None)
            return hid, False, str(e)
        return hid, True, ""

    verb = f"blocked {needle}" if block else f"unblocked {needle}"
    return await fleet_fan_out(_instances(), _one, app_label="Pi-hole",
                               verb=verb, log_tag="pihole",
                               log_extra=f"action={'block' if block else 'unblock'} domain={needle}")


async def _add_regex_deny(base: str, sid: str, pattern: str) -> None:
    """Add a REGEX/wildcard denylist rule via POST /api/domains/deny/regex
    ``{domain: <pattern>}``. Idempotent — a 409 'already exists' is the
    requested end-state, so treated as success. Raises RuntimeError otherwise."""
    try:
        async with httpx.AsyncClient(verify=False, timeout=12.0, follow_redirects=True) as cli:
            r = await cli.post(base + "/api/domains/deny/regex",
                               json={"domain": pattern}, headers={"X-FTL-SID": sid})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        raise RuntimeError(f"{type(e).__name__}: {e}")
    if r.status_code in (401, 403):
        raise RuntimeError(f"auth failed: HTTP {r.status_code}")
    if r.status_code in (200, 201, 204) or r.status_code == 409:
        return
    raise RuntimeError(f"HTTP {r.status_code}")


async def _skill_block_regex(pattern: str) -> dict:
    """Add a regex/wildcard block rule on EVERY Pi-hole instance (fleet write).
    Complements the exact pihole_block_domain — one regex catches a whole family
    (e.g. all of a brand's tracker subdomains)."""
    needle = (pattern or "").strip()
    if not needle:
        return {"ok": False, "status": 0,
                "detail": "no regex given (say e.g. \"block regex (^|\\\\.)ads\\\\..*\")"}

    # noinspection DuplicatedCode
    async def _one(hid, _sidx, hrow, chip):
        password = _password(chip)
        base = resolve_base_url(hrow, chip)
        if not (password and base):
            return hid, False, "no creds / url"
        try:
            sid = await _get_sid(base, password)
            await _add_regex_deny(base, sid, needle)
        except RuntimeError as e:
            if "auth failed" in str(e):
                _sid_cache.pop(_sid_key(resolve_base_url(hrow, chip)), None)
            return hid, False, str(e)
        return hid, True, ""

    return await fleet_fan_out(_instances(), _one, app_label="Pi-hole",
                               verb=f"added regex block {needle}", log_tag="pihole",
                               log_extra=f"action=block_regex pattern={needle}")


# noinspection PyUnusedLocal
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None,
                    arg: Optional[str] = None, **_kw) -> dict:
    """Dispatch one Pi-hole skill. Action skills (enable / disable* / refresh /
    reenable / block / unblock) FAN OUT to every Pi-hole instance regardless of
    the targeted chip — fleet-level by design, so the per-chip args the route
    passes are intentionally unused (the registry contract requires the
    signature). ``pihole_status`` is read-only. Raises ValueError on an unknown
    skill id."""
    if skill_id == "pihole_block_domain":
        return await _skill_set_domain(arg or "", block=True)
    if skill_id == "pihole_unblock_domain":
        return await _skill_set_domain(arg or "", block=False)
    if skill_id == "pihole_block_regex":
        return await _skill_block_regex(arg or "")
    return await fleet_run_skill(skill_id, prefix="pihole",
                                 status_fn=_skill_status,
                                 action_fn=_skill_fleet_action, skills=SKILLS)
