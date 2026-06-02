"""AdGuard Home per-app module.

Encapsulates everything AdGuard-Home-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic. Public surface mirrors
the per-app contract documented in ``logic/apps/registry.py`` /
``speedtest_tracker.py``:

    SLUGS               — catalog slugs this module handles.
    requires_api_key()  — True (the chip's ``api_key`` field stores the
                          AdGuard *password*; username is a separate
                          plain ``username`` chip field).
    resolve_base_url(host_row, chip) -> str
    test_credential(host_row, chip, candidate_key, *, payload=None) -> dict
    fetch_data(host_row, chip, *, host_id, service_idx, force) -> dict
    peek_latest(host_id, service_idx) -> dict | None
    SKILLS              — fleet-wide actions (status / enable / disable /
                          disable_<preset> / refresh / reenable).
    run_skill(...)      — FANS OUT to EVERY AdGuard instance in
                          hosts_config (the actions are fleet-level by
                          design — operator decision), then aggregates.

Auth model
----------
AdGuard's control API (``/control/*``) uses HTTP Basic auth. Each
curated host carries its OWN username + password (the operator runs N
independent AdGuard hosts). The password is the secret and lives in the
chip's ``api_key`` field (so all the existing keep-current / ``_set`` /
test-credential plumbing applies unchanged); the username is a plain,
non-secret ``username`` chip field returned to the SPA editor.

Aggregation
-----------
The Apps view aggregates the N AdGuard instances into ONE card. This
module's ``fetch_data`` returns PER-HOST stats; the SPA's app-level
extras block (``adguardhome_extras.html`` + ``adguardAggregate`` in
``static/js/apps/adguardhome.js``) sums them across hosts. ``run_skill``
is the backend half of the fleet model — it loops every instance.

Endpoints used (AdGuard Home control API):
    GET  /control/status            — protection on/off + version
    GET  /control/stats             — queries / blocked / avg time / tops
    GET  /control/filtering/status  — filters[].rules_count (blocklist)
    POST /control/protection        — {enabled, duration_ms}  (v0.107+)
    POST /control/dns_config        — {protection_enabled}    (fallback)
    POST /control/filtering/refresh — {whitelist:false}       (blocklists)

API reference: https://github.com/AdguardTeam/AdGuardHome/tree/master/openapi
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import httpx

from logic.apps._common import cache_key, fetch_preamble, resolve_base_url
from logic.coerce import safe_float, safe_int

# Catalog template slugs handled by this module. The catalog ships
# ``adguard-home``; the aliases catch operator-edited chips that kept
# the brand but dropped the catalog link.
SLUGS: tuple[str, ...] = ("adguard-home", "adguardhome", "adguard")

# Timed-disable presets (label, seconds). Each becomes a distinct
# ``adguard_disable_<label>`` skill so the AI / Telegram / drawer can
# pick a duration without a params channel (the skill route carries no
# body). 0 / indefinite is the bare ``adguard_disable`` skill.
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
        "id": f"adguard_disable_{label}",
        "name": f"Disable for {human}",
        "ai_phrases": (f"disable adguard for {human}, pause blocking for {human}, "
                       f"turn off protection for {human}"),
        "destructive": True,
        # Non-protocol hint consumed by the SPA to group the timed-disable
        # buttons + by run_skill to resolve the duration.
        "disable_seconds": seconds,
    }


# Fleet-wide SKILLS. run_skill ignores the targeted chip's identity for
# the action skills — it fans out to EVERY AdGuard instance (operator's
# explicit "all the fleet" requirement). `adguard_status` is read-only.
SKILLS: tuple[dict, ...] = (
    {
        "id": "adguard_status",
        "name": "Show AdGuard status",
        "ai_phrases": ("adguard status, adguard stats, dns blocking stats, "
                       "how many queries blocked today, blocked percentage, "
                       "show adguard, ad blocking summary, pihole status"),
        "destructive": False,
    },
    {
        "id": "adguard_enable",
        "name": "Enable protection",
        "ai_phrases": ("enable adguard, turn on protection, enable dns blocking, "
                       "turn adguard on, start blocking ads"),
        "destructive": False,
    },
    {
        "id": "adguard_disable",
        "name": "Disable protection",
        "ai_phrases": ("disable adguard, turn off protection, pause ad blocking, "
                       "stop blocking, turn adguard off indefinitely"),
        "destructive": True,
    },
    *(_disable_skill(lbl, sec) for lbl, sec in DISABLE_PRESETS),
    {
        "id": "adguard_refresh",
        "name": "Refresh blocklists",
        "ai_phrases": ("refresh adguard blocklists, update filter lists, "
                       "refresh dns filters, update adguard lists"),
        "destructive": False,
    },
    {
        "id": "adguard_reenable",
        "name": "Re-enable (cancel timed disable)",
        "ai_phrases": ("cancel timed disable, re-enable adguard now, "
                       "turn protection back on, undo disable"),
        "destructive": False,
    },
)

# Per-app modules intentionally share the cache + requires_api_key +
# resolve_base_url shape — duplication is the documented price of full
# per-app encapsulation (PyCharm flags it Info-level; not suppressible).
CACHE_TTL_S = 30
_data_cache: dict[str, tuple[float, dict]] = {}


def requires_api_key() -> bool:
    """True — the chip's ``api_key`` field carries the AdGuard password;
    the editor MUST render the username + password inputs + Test."""
    return True


def _creds(chip: dict, *, password: Optional[str] = None,
           username: Optional[str] = None) -> tuple[str, str]:
    """Resolve (username, password) for a chip. Explicit args win (a
    pre-save test passes the candidate values); else fall back to the
    stored chip fields."""
    u = (username if username is not None else "").strip() or (chip.get("username") or "").strip()
    p = (password if password is not None else "").strip() or (chip.get("api_key") or "").strip()
    return u, p


async def test_credential(host_row: dict, chip: dict, candidate_key: str, *,
                          payload: Optional[dict] = None, **_kw) -> dict:
    """Probe ``GET /control/status`` with the candidate Basic-auth
    credentials. ``candidate_key`` is the password; the username comes
    from the test payload (pre-save) or the stored chip. Returns
    ``{ok, detail, status}``."""
    pay = payload or {}
    username, password = _creds(
        chip,
        password=(candidate_key or "").strip() or None,
        username=(pay.get("username") or "").strip() or None,
    )
    if not password:
        return {"ok": False, "detail": "password required", "status": 0}
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "detail": "no upstream URL configured", "status": 0}
    url = base + "/control/status"
    try:
        # follow_redirects=True: AdGuard (or a reverse proxy in front of it)
        # commonly 307/308-redirects /control/status (http->https, or a
        # trailing-slash / sub-path normalisation). Without following, the
        # redirect surfaced to the operator as a bare "HTTP 307".
        async with httpx.AsyncClient(verify=False, timeout=10.0, follow_redirects=True,
                                     auth=httpx.BasicAuth(username, password)) as cli:
            r = await cli.get(url, headers={"Accept": "application/json"})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[adguard] warning: test-connection {url} failed — {type(e).__name__}: {e}")
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    # Log the resolved URL + final status (+ the redirect chain when the
    # upstream bounced) so a failed test is diagnosable from Admin -> Logs
    # without exposing the password.
    redirects = " <- ".join(str(h.url) for h in r.history) if r.history else ""
    print(f"[adguard] INFO test-connection url={url} -> HTTP {r.status_code} "
          f"final={r.url}{(' via ' + redirects) if redirects else ''}")
    if r.status_code == 200:
        ver = ""
        try:
            ver = str((r.json() or {}).get("version") or "").strip()
        except (ValueError, TypeError):  # noqa: BLE001
            pass
        return {"ok": True, "detail": f"OK{(' — ' + ver) if ver else ''}", "status": 200}
    if r.status_code in (401, 403):
        return {"ok": False, "detail": "auth failed (check username / password)",
                "status": r.status_code}
    if r.status_code in (301, 302, 307, 308):
        loc = r.headers.get("location") or "?"
        return {"ok": False,
                "detail": f"HTTP {r.status_code} redirect to {loc} — check the URL "
                          f"scheme (http vs https) / port",
                "status": r.status_code}
    return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}


def _top_entry(items: Any) -> Optional[dict]:
    """AdGuard ``top_blocked_domains`` / ``top_clients`` rows are either
    single-key dicts ``{"<name>": <count>}`` or ``{"name", "count"}``.
    Return the first as ``{name, count}`` or None."""
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    if not isinstance(first, dict) or not first:
        return None
    if "name" in first or "count" in first:
        return {"name": str(first.get("name") or ""), "count": safe_int(first.get("count"))}
    for k, v in first.items():
        return {"name": str(k), "count": safe_int(v)}
    return None


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch ONE AdGuard host's current stats. Returns the per-host
    shape the SPA aggregates across instances. Raises ``ValueError``
    (→ HTTP 400) when creds / URL are missing, ``RuntimeError``
    (→ HTTP 502) on an upstream failure (the SPA aggregate footnotes
    the failing host, so a single down host doesn't sink the card)."""
    username, password = _creds(chip)
    if not password:
        raise ValueError("password not set for this instance")
    now = time.time()
    base, hit = fetch_preamble(host_row, chip, host_id, service_idx,
                               _data_cache, CACHE_TTL_S, now, force)
    if hit is not None:
        return hit
    print(f"[adguard] INFO fetch host={host_id} svc_idx={service_idx} url={base}/control/stats")
    auth = httpx.BasicAuth(username, password)
    headers = {"Accept": "application/json"}

    async def _get(path: str) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(verify=False, timeout=12.0, auth=auth, follow_redirects=True) as cli:
                r = await cli.get(base + path, headers=headers)
        except (httpx.HTTPError, OSError) as exc:  # noqa: BLE001
            raise RuntimeError(f"{type(exc).__name__}: {exc}")
        if r.status_code in (401, 403):
            raise RuntimeError(f"auth failed: HTTP {r.status_code} (check username / password)")
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} for {base + path}")
        try:
            return r.json()
        except (ValueError, TypeError):  # noqa: BLE001
            raise RuntimeError(f"non-JSON from {base + path}")

    try:
        stats, status, filt = await asyncio.gather(
            _get("/control/stats"),
            _get("/control/status"),
            _get("/control/filtering/status"),
        )
    except RuntimeError as e:
        print(f"[adguard] error: fetch host={host_id} failed — {e}")
        raise

    stats = stats or {}
    status = status or {}
    filt = filt or {}

    queries = safe_int(stats.get("num_dns_queries"))
    blocked = (safe_int(stats.get("num_blocked_filtering"))
               + safe_int(stats.get("num_replaced_safebrowsing"))
               + safe_int(stats.get("num_replaced_parental")))
    blocked_pct = round((blocked / queries) * 100.0, 2) if queries > 0 else 0.0
    avg_ms = round(safe_float(stats.get("avg_processing_time")) * 1000.0, 2)
    top_blocked = _top_entry(stats.get("top_blocked_domains"))
    clients = stats.get("top_clients")
    num_clients = len(clients) if isinstance(clients, list) else 0

    rules = 0
    filters = filt.get("filters")
    if isinstance(filters, list):
        for f in filters:
            if isinstance(f, dict) and f.get("enabled"):
                rules += safe_int(f.get("rules_count"))

    out: dict = {
        "ok": True,
        "host": str(host_row.get("label") or host_id),
        "host_id": host_id,
        "protection_enabled": bool(status.get("protection_enabled", True)),
        "protection_disabled_duration_ms": safe_int(status.get("protection_disabled_duration")),
        "queries_today": queries,
        "blocked_today": blocked,
        "blocked_pct": blocked_pct,
        "blocklist_rules": rules,
        "num_clients": num_clients,
        "avg_processing_ms": avg_ms,
        "top_blocked_domain": top_blocked,
        "version": str(status.get("version") or "").strip(),
        "fetched_at": int(now),
    }
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``. Returns the last fetched per-host stats or
    None."""
    cached = _data_cache.get(cache_key(host_id, service_idx))
    return cached[1] if cached else None


# ---------------------------------------------------------------------------
# Fleet actions (run_skill). Every action skill fans out to EVERY AdGuard
# instance in hosts_config — the operator's explicit "all the fleet" model.
# ---------------------------------------------------------------------------
def _instances() -> list:
    """Enumerate every AdGuard instance with credentials:
    ``[(host_id, service_idx, host_row, chip)]``. Never raises."""
    # noinspection PyBroadException
    try:
        from logic.apps.registry import instances_for_slug  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    out = []
    for slug in SLUGS:
        for tup in instances_for_slug(slug):
            out.append(tup)
    # Dedup by (host_id, service_idx) in case aliases overlap.
    seen = set()
    uniq = []
    for hid, sidx, hrow, chip in out:
        k = (hid, sidx)
        if k in seen:
            continue
        seen.add(k)
        uniq.append((hid, sidx, hrow, chip))
    return uniq


async def _set_protection(base: str, auth: httpx.BasicAuth, enabled: bool,
                          duration_ms: int = 0) -> None:
    """POST /control/protection {enabled, duration}; fall back to the
    older /control/dns_config {protection_enabled} on 404/405. Raises
    RuntimeError on failure."""
    body: dict[str, Any] = {"enabled": bool(enabled)}
    if not enabled and duration_ms > 0:
        body["duration"] = int(duration_ms)
    try:
        async with httpx.AsyncClient(verify=False, timeout=12.0, auth=auth, follow_redirects=True) as cli:
            r = await cli.post(base + "/control/protection", json=body)
            if r.status_code in (404, 405):
                # Older AdGuard — no /control/protection; dns_config has no
                # timed-disable support, so duration is silently dropped.
                r = await cli.post(base + "/control/dns_config",
                                   json={"protection_enabled": bool(enabled)})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        raise RuntimeError(f"{type(e).__name__}: {e}")
    if r.status_code in (401, 403):
        raise RuntimeError(f"auth failed: HTTP {r.status_code}")
    if r.status_code not in (200, 204):
        raise RuntimeError(f"HTTP {r.status_code}")


async def _refresh_filters(base: str, auth: httpx.BasicAuth) -> None:
    """POST /control/filtering/refresh {whitelist:false}. Raises on failure."""
    try:
        async with httpx.AsyncClient(verify=False, timeout=30.0, auth=auth, follow_redirects=True) as cli:
            r = await cli.post(base + "/control/filtering/refresh",
                               json={"whitelist": False})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        raise RuntimeError(f"{type(e).__name__}: {e}")
    if r.status_code in (401, 403):
        raise RuntimeError(f"auth failed: HTTP {r.status_code}")
    if r.status_code not in (200, 204):
        raise RuntimeError(f"HTTP {r.status_code}")


def _fmt_int(v) -> str:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return "—"


async def _skill_status() -> dict:
    """Read-only: live-fetch every instance + aggregate into a formatted
    detail block (web inline + Telegram + AI). Never raises."""
    insts = _instances()
    if not insts:
        return {"ok": False, "detail": "no AdGuard instances configured", "status": 0}
    results = await asyncio.gather(
        *[fetch_data(hrow, chip, host_id=hid, service_idx=sidx, force=True)
          for (hid, sidx, hrow, chip) in insts],
        return_exceptions=True,
    )
    ok_rows = [r for r in results if isinstance(r, dict) and r.get("ok")]
    failed = [insts[i][0] for i, r in enumerate(results) if not (isinstance(r, dict) and r.get("ok"))]
    if not ok_rows:
        return {"ok": False, "detail": "all AdGuard hosts unreachable", "status": 0}
    queries = sum(safe_int(r.get("queries_today")) for r in ok_rows)
    blocked = sum(safe_int(r.get("blocked_today")) for r in ok_rows)
    pct = round((blocked / queries) * 100.0, 1) if queries > 0 else 0.0
    rules = max((safe_int(r.get("blocklist_rules")) for r in ok_rows), default=0)
    clients = sum(safe_int(r.get("num_clients")) for r in ok_rows)
    # Query-weighted average processing time.
    wsum = sum(safe_float(r.get("avg_processing_ms")) * safe_int(r.get("queries_today")) for r in ok_rows)
    avg_ms = round(wsum / queries, 1) if queries > 0 else 0.0
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
        f"🛡️ AdGuard — {n} host{'s' if n != 1 else ''}",
        f"⛔ Blocked today: {_fmt_int(blocked)}  ({pct}%)",
        f"🔢 Queries today: {_fmt_int(queries)}",
        f"📋 Blocklist domains: {_fmt_int(rules)}",
        f"👥 Active clients: {_fmt_int(clients)}",
        f"⏱️ Avg processing: {avg_ms} ms",
    ]
    if top:
        lines.append(f"🔝 Top blocked: {top.get('name')} ({_fmt_int(top.get('count'))})")
    lines.append(f"🔐 Protection: {'ON' if prot_on == n else f'{prot_on}/{n} ON'}")
    if failed:
        lines.append(f"⚠️ unreachable: {', '.join(failed)}")
    return {"ok": True, "detail": "\n".join(lines), "status": 200}


async def _skill_fleet_action(action: str, duration_ms: int = 0) -> dict:
    """Apply enable / disable / refresh across EVERY instance. Returns
    ``{ok, detail}`` with an ok/failed tally."""
    insts = _instances()
    if not insts:
        return {"ok": False, "detail": "no AdGuard instances configured", "status": 0}

    async def _one(hid, _sidx, hrow, chip):
        username, password = _creds(chip)
        base = resolve_base_url(hrow, chip)
        if not (password and base):
            return hid, False, "no creds / url"
        auth = httpx.BasicAuth(username, password)
        try:
            if action == "enable":
                await _set_protection(base, auth, True)
            elif action == "disable":
                await _set_protection(base, auth, False, duration_ms)
            elif action == "refresh":
                await _refresh_filters(base, auth)
            else:
                return hid, False, f"unknown action {action}"
        except RuntimeError as e:
            return hid, False, str(e)
        return hid, True, ""

    results = await asyncio.gather(*[_one(*t) for t in insts])
    ok_hosts = [hid for hid, ok, _ in results if ok]
    bad = [(hid, err) for hid, ok, err in results if not ok]
    verb = {"enable": "enabled", "disable": "disabled", "refresh": "refreshed"}.get(action, action)
    if action == "disable" and duration_ms > 0:
        secs = duration_ms // 1000
        verb = f"disabled for {secs}s"
    detail = f"AdGuard {verb} on {len(ok_hosts)}/{len(results)} host(s)"
    if bad:
        detail += " — failed: " + ", ".join(f"{h} ({e})" for h, e in bad)
    print(f"[adguard] INFO fleet action={action} dur_ms={duration_ms} "
          f"ok={len(ok_hosts)}/{len(results)}")
    return {"ok": len(ok_hosts) > 0, "detail": detail, "status": 200 if ok_hosts else 502}


# noinspection PyUnusedLocal
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None, **_kw) -> dict:
    """Dispatch one AdGuard skill. Action skills (enable / disable* /
    refresh / reenable) FAN OUT to every AdGuard instance regardless of
    the targeted chip — they are fleet-level by design, so the per-chip
    ``host_row`` / ``chip`` / ``host_id`` / ``service_idx`` the route
    passes are intentionally unused here (the registry contract requires
    the signature). ``adguard_status`` is read-only. Raises ValueError on
    an unknown skill id."""
    if skill_id == "adguard_status":
        return await _skill_status()
    if skill_id in ("adguard_enable", "adguard_reenable"):
        return await _skill_fleet_action("enable")
    if skill_id == "adguard_disable":
        return await _skill_fleet_action("disable")
    if skill_id == "adguard_refresh":
        return await _skill_fleet_action("refresh")
    if skill_id.startswith("adguard_disable_"):
        label = skill_id[len("adguard_disable_"):]
        secs = dict(DISABLE_PRESETS).get(label)
        if secs is None:
            raise ValueError(f"unknown disable preset: {label!r}")
        return await _skill_fleet_action("disable", secs * 1000)
    raise ValueError(f"unknown skill: {skill_id!r}")
