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
import hashlib
import time
from typing import Any, Optional

import httpx

from logic.apps._common import (cache_key, fetch_preamble, fleet_instances,
                                fmt_int_grouped, peek_cache, resolve_base_url)
from logic.coerce import safe_float, safe_int

# Catalog template slugs handled by this module. The catalog ships
# ``pihole``; the aliases catch operator-edited chips that kept the brand
# but dropped the catalog link.
SLUGS: tuple[str, ...] = ("pihole", "pi-hole", "pihole-v6")

# Timed-disable presets (label, seconds) — Pi-hole's blocking timer
# natively auto-re-enables after N seconds, so each preset is a distinct
# ``pihole_disable_<label>`` skill (the skill route carries no params).
DISABLE_PRESETS: tuple[tuple[str, int], ...] = (
    ("1m", 60), ("5m", 300), ("10m", 600), ("30m", 1800),
    ("1h", 3600), ("2h", 7200), ("24h", 86400),
)


def _disable_skill(label: str, seconds: int) -> dict:
    human = {
        "1m": "1 minute", "5m": "5 minutes", "10m": "10 minutes",
        "30m": "30 minutes", "1h": "1 hour", "2h": "2 hours", "24h": "24 hours",
    }.get(label, label)
    return {
        "id": f"pihole_disable_{label}",
        "name": f"Disable for {human}",
        "ai_phrases": (f"disable pihole for {human}, pause blocking for {human}, "
                       f"turn off pi-hole for {human}"),
        "destructive": True,
        # Non-protocol hint consumed by the SPA to group the timed-disable
        # buttons + by run_skill to resolve the duration.
        "disable_seconds": seconds,
    }


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
    *(_disable_skill(lbl, sec) for lbl, sec in DISABLE_PRESETS),
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
)

# Module-wide fleet flag — the registry stamps ``fleet: True`` onto each
# skill so the Telegram slash command + AI dispatch run host-less.
FLEET_SKILLS: bool = True

# Per-app modules intentionally share the cache + requires_api_key +
# resolve_base_url shape (PyCharm flags the duplicate Info-level; not
# suppressible). 30s data cache mirrors AdGuard.
CACHE_TTL_S = 30
_data_cache: dict[str, tuple[float, dict]] = {}

# Session-ID cache: base|password-hash -> (sid, expires_at). Pi-hole caps
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


def _sid_key(base: str, password: str) -> str:
    return base + "|" + hashlib.sha256(password.encode()).hexdigest()[:16]


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
    _sid_cache[_sid_key(base, password)] = (sid, time.time() + max(60, validity - 60))
    return sid


async def _get_sid(base: str, password: str) -> str:
    """Return a valid SID — reuse the cached one until ~expiry, else
    authenticate. Raises RuntimeError on auth failure."""
    cached = _sid_cache.get(_sid_key(base, password))
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
    ver = info.get("version") if isinstance(info.get("version"), dict) else info
    core = (ver or {}).get("core") if isinstance(ver, dict) else None
    if isinstance(core, dict):
        local = core.get("local") if isinstance(core.get("local"), dict) else core
        v = str((local or {}).get("version") or "").strip()
        if v:
            return v
    return ""


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch ONE Pi-hole host's current stats — same per-host shape the
    SPA aggregates across instances (parallel to AdGuard). Raises
    ``ValueError`` (-> HTTP 400) when the password / URL is missing,
    ``RuntimeError`` (-> HTTP 502) on an upstream failure."""
    password = _password(chip)
    if not password:
        raise ValueError("password not set for this instance")
    now = time.time()
    base, hit = fetch_preamble(host_row, chip, host_id, service_idx,
                               _data_cache, CACHE_TTL_S, now, force)
    if hit is not None:
        return hit
    print(f"[pihole] INFO fetch host={host_id} svc_idx={service_idx} url={base}/api/stats/summary")

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
            _sid_cache.pop(_sid_key(base, password), None)
            sid = await _authenticate(base, password)
            sc, summary = await _authed_get(sid, "/api/stats/summary")
        if sc != 200 or not isinstance(summary, dict):
            raise RuntimeError(f"HTTP {sc} for {base}/api/stats/summary")
        # Blocking status + top blocked + version — best-effort in parallel.
        (_bc, blocking), (_tc, topd), (_vc, vinfo) = await asyncio.gather(
            _authed_get(sid, "/api/dns/blocking"),
            _authed_get(sid, "/api/stats/top_domains?blocked=true&count=1"),
            _authed_get(sid, "/api/info/version"),
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

    top_blocked = None
    if isinstance(topd, dict):
        domains = topd.get("domains")
        if isinstance(domains, list) and domains and isinstance(domains[0], dict):
            top_blocked = {"name": str(domains[0].get("domain") or ""),
                           "count": safe_int(domains[0].get("count"))}

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
        "version": _version_str(vinfo),
        "fetched_at": int(now),
    }
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


async def _skill_status() -> dict:
    """Read-only: live-fetch every instance + aggregate into a formatted
    detail block (web inline + Telegram + AI). Never raises."""
    insts = _instances()
    if not insts:
        return {"ok": False, "detail": "no Pi-hole instances configured", "status": 0}
    results = await asyncio.gather(
        *[fetch_data(hrow, chip, host_id=hid, service_idx=sidx, force=True)
          for (hid, sidx, hrow, chip) in insts],
        return_exceptions=True,
    )
    ok_rows = [r for r in results if isinstance(r, dict) and r.get("ok")]
    failed = [insts[i][0] for i, r in enumerate(results)
              if not (isinstance(r, dict) and r.get("ok"))]
    if not ok_rows:
        return {"ok": False, "detail": "all Pi-hole hosts unreachable", "status": 0}
    queries = sum(safe_int(r.get("queries_today")) for r in ok_rows)
    blocked = sum(safe_int(r.get("blocked_today")) for r in ok_rows)
    pct = round((blocked / queries) * 100.0, 1) if queries > 0 else 0.0
    rules = max((safe_int(r.get("blocklist_rules")) for r in ok_rows), default=0)
    clients = sum(safe_int(r.get("num_clients")) for r in ok_rows)
    top = None
    top_count = -1
    for r in ok_rows:
        t = r.get("top_blocked_domain")
        if not (isinstance(t, dict) and t.get("name")):
            continue
        c = safe_int(t.get("count"))
        if c > top_count:
            top, top_count = t, c
    prot_on = sum(1 for r in ok_rows if r.get("protection_enabled"))
    n = len(ok_rows)
    lines = [
        f"🕳️ Pi-hole — {n} host{'s' if n != 1 else ''}",
        f"⛔ Blocked today: {fmt_int_grouped(blocked)}  ({pct}%)",
        f"🔢 Queries today: {fmt_int_grouped(queries)}",
        f"📋 Blocklist domains: {fmt_int_grouped(rules)}",
        f"👥 Active clients: {fmt_int_grouped(clients)}",
    ]
    if top:
        lines.append(f"🔝 Top blocked: {top.get('name')} ({fmt_int_grouped(top.get('count'))})")
    lines.append(f"🔐 Blocking: {'ON' if prot_on == n else f'{prot_on}/{n} ON'}")
    if failed:
        lines.append(f"⚠️ unreachable: {', '.join(failed)}")
    return {"ok": True, "detail": "\n".join(lines), "status": 200}


async def _skill_fleet_action(action: str, timer_s: int = 0) -> dict:
    """Apply enable / disable / refresh across EVERY instance. Returns
    ``{ok, detail}`` with an ok/failed tally."""
    insts = _instances()
    if not insts:
        return {"ok": False, "detail": "no Pi-hole instances configured", "status": 0}

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
                await _set_blocking(base, sid, False, timer_s)
            elif action == "refresh":
                await _run_gravity(base, sid)
            else:
                return hid, False, f"unknown action {action}"
        except RuntimeError as e:
            # A stale cached SID surfaces as "auth failed" — drop + one retry.
            if "auth failed" in str(e):
                _sid_cache.pop(_sid_key(resolve_base_url(hrow, chip), password), None)
            return hid, False, str(e)
        return hid, True, ""

    results = await asyncio.gather(*[_one(*t) for t in insts])
    ok_hosts = [hid for hid, ok, _ in results if ok]
    bad = [(hid, err) for hid, ok, err in results if not ok]
    verb = {"enable": "enabled", "disable": "disabled", "refresh": "gravity updated"}.get(action, action)
    if action == "disable" and timer_s > 0:
        verb = f"disabled for {timer_s}s"
    detail = f"Pi-hole {verb} on {len(ok_hosts)}/{len(results)} host(s)"
    if bad:
        detail += " — failed: " + ", ".join(f"{h} ({e})" for h, e in bad)
    print(f"[pihole] INFO fleet action={action} timer_s={timer_s} "
          f"ok={len(ok_hosts)}/{len(results)}")
    return {"ok": len(ok_hosts) > 0, "detail": detail, "status": 200 if ok_hosts else 502}


# noinspection PyUnusedLocal
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None, **_kw) -> dict:
    """Dispatch one Pi-hole skill. Action skills (enable / disable* /
    refresh / reenable) FAN OUT to every Pi-hole instance regardless of
    the targeted chip — fleet-level by design, so the per-chip args the
    route passes are intentionally unused (the registry contract requires
    the signature). ``pihole_status`` is read-only. Raises ValueError on
    an unknown skill id."""
    if skill_id == "pihole_status":
        return await _skill_status()
    if skill_id in ("pihole_enable", "pihole_reenable"):
        return await _skill_fleet_action("enable")
    if skill_id == "pihole_disable":
        return await _skill_fleet_action("disable")
    if skill_id == "pihole_refresh":
        return await _skill_fleet_action("refresh")
    if skill_id.startswith("pihole_disable_"):
        label = skill_id[len("pihole_disable_"):]
        secs = dict(DISABLE_PRESETS).get(label)
        if secs is None:
            raise ValueError(f"unknown disable preset: {label!r}")
        return await _skill_fleet_action("disable", secs)
    raise ValueError(f"unknown skill: {skill_id!r}")
