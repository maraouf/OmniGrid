"""Shared leaf helpers for authenticated per-app modules.

Per-app modules under ``logic/apps/<slug>.py`` stay self-contained for
their APP-SPECIFIC logic (auth probe, payload parsing, SKILLS,
run_skill). The GENERIC plumbing every credentialed app repeats —
resolving a chip's upstream base URL + the per-(host, service) fetch
cache — lives here so the structural duplication doesn't accumulate
across modules. Same precedent as ``logic/coerce.py`` (numeric coercion
shared by the same modules): a dependency-free leaf import, no cycle.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from logic.coerce import safe_int

# Canonical timed-disable presets (label, seconds) shared by every fleet
# DNS-blocker (Pi-hole / AdGuard / future). The provider's blocking timer
# natively auto-re-enables after N seconds.
_FLEET_DISABLE_PRESETS: "tuple[tuple[str, int], ...]" = (
    ("1m", 60), ("5m", 300), ("10m", 600), ("30m", 1800),
    ("1h", 3600), ("2h", 7200), ("24h", 86400),
)
_FLEET_DISABLE_HUMAN = {
    "1m": "1 minute", "5m": "5 minutes", "10m": "10 minutes",
    "30m": "30 minutes", "1h": "1 hour", "2h": "2 hours", "24h": "24 hours",
}


def fleet_disable_skills(prefix: str, app_label: str) -> list:
    """Build the timed-disable skill dicts (1m … 24h) for a fleet
    DNS-blocker. ``prefix`` is the skill-id stem (``"pihole"`` →
    ``"pihole_disable_5m"``); ``app_label`` is the lowercase brand used
    in the AI match phrases. Each skill carries ``disable_seconds`` so
    ``run_skill`` resolves the duration from the SKILLS list directly
    (no parallel presets tuple needed). Replaces the per-module
    ``DISABLE_PRESETS`` + ``_disable_skill`` factory."""
    out = []
    for label, seconds in _FLEET_DISABLE_PRESETS:
        human = _FLEET_DISABLE_HUMAN.get(label, label)
        out.append({
            "id": f"{prefix}_disable_{label}",
            "name": f"Disable for {human}",
            "ai_phrases": (f"disable {app_label} for {human}, pause blocking "
                           f"for {human}, turn off {app_label} for {human}"),
            "destructive": True,
            "disable_seconds": seconds,
        })
    return out


async def fleet_fetch_all(insts: list, fetch_fn) -> "tuple[list, list]":
    """Force-fetch every fleet instance in parallel and partition the
    results into ``(ok_rows, failed_host_ids)``. ``insts`` is the
    ``fleet_instances`` shape ``[(host_id, service_idx, host_row, chip)]``;
    ``fetch_fn`` is the module's ``fetch_data`` (called with
    ``host_id=`` / ``service_idx=`` / ``force=True``). Exceptions per
    instance are swallowed into the failed list (gather
    ``return_exceptions=True``)."""
    results = await asyncio.gather(
        *[fetch_fn(hrow, chip, host_id=hid, service_idx=sidx, force=True)
          for (hid, sidx, hrow, chip) in insts],
        return_exceptions=True,
    )
    ok_rows = [r for r in results if isinstance(r, dict) and r.get("ok")]
    failed = [insts[i][0] for i, r in enumerate(results)
              if not (isinstance(r, dict) and r.get("ok"))]
    return ok_rows, failed


def fleet_sum(rows: list, field: str) -> int:
    """Sum an integer field across fleet rows (missing / non-numeric → 0)."""
    return sum(safe_int(r.get(field)) for r in rows)


def fleet_max(rows: list, field: str) -> int:
    """Max of an integer field across fleet rows (``0`` when empty)."""
    return max((safe_int(r.get(field)) for r in rows), default=0)


def fleet_top(rows: list, key: str) -> Optional[dict]:
    """Pick the single ``{name, count}`` entry with the highest ``count``
    across every fleet row's ``rows[i][key]`` sub-dict (e.g. the top
    blocked domain across all hosts). ``None`` when no row carries one."""
    top = None
    top_count = -1
    for r in rows:
        t = r.get(key)
        if not (isinstance(t, dict) and t.get("name")):
            continue
        c = safe_int(t.get("count"))
        if c > top_count:
            top, top_count = t, c
    return top


def fleet_action_result(results: list, app_label: str, verb: str) -> dict:
    """Format a fleet action's per-host ``(host_id, ok, err)`` tuples into
    the standard ``{ok, detail, status}`` envelope: ``"<app> <verb> on
    K/N host(s)"`` plus a ``— failed: …`` tail for any failures. ``ok`` is
    true when at least one host succeeded."""
    ok_hosts = [hid for hid, ok, _ in results if ok]
    bad = [(hid, err) for hid, ok, err in results if not ok]
    detail = f"{app_label} {verb} on {len(ok_hosts)}/{len(results)} host(s)"
    if bad:
        detail += " — failed: " + ", ".join(f"{h} ({e})" for h, e in bad)
    return {"ok": len(ok_hosts) > 0, "detail": detail,
            "status": 200 if ok_hosts else 502}


def fleet_blocker_totals(ok_rows: list) -> dict:
    """Standard DNS-blocker fleet aggregate over the per-host rows (the
    `host_*` shape both Pi-hole + AdGuard emit, same field names): sum
    queries / blocked / clients, max blocklist rules, blocked-percent,
    the single top-blocked domain across hosts, count of hosts with
    protection ON, and the reachable-host count. Returns one dict the
    status renderer reads — keeps the metric math in ONE place."""
    queries = fleet_sum(ok_rows, "queries_today")
    blocked = fleet_sum(ok_rows, "blocked_today")
    return {
        "queries": queries,
        "blocked": blocked,
        "pct": round((blocked / queries) * 100.0, 1) if queries > 0 else 0.0,
        "rules": fleet_max(ok_rows, "blocklist_rules"),
        "clients": fleet_sum(ok_rows, "num_clients"),
        "top": fleet_top(ok_rows, "top_blocked_domain"),
        "prot_on": sum(1 for r in ok_rows if r.get("protection_enabled")),
        "n": len(ok_rows),
    }


def fleet_blocker_detail(totals: dict, failed: list, *, header: str,
                         protection_label: str,
                         extra_lines: "Optional[list]" = None) -> dict:
    """Render a DNS-blocker fleet status into the ``{ok, detail, status}``
    envelope. ``header`` is the emoji + app-name lead (e.g. ``"🕳️ Pi-hole"``
    / ``"🛡️ AdGuard"``); ``protection_label`` is the on/off noun
    (``"Blocking"`` / ``"Protection"``); ``extra_lines`` are app-specific
    stat lines inserted after the core stats (e.g. AdGuard's avg-processing
    line — Pi-hole passes none). ``totals`` is ``fleet_blocker_totals``
    output."""
    n = totals["n"]
    on = totals["prot_on"]
    lines = [
        f"{header} — {n} host{'s' if n != 1 else ''}",
        f"⛔ Blocked today: {fmt_int_grouped(totals['blocked'])}  ({totals['pct']}%)",
        f"🔢 Queries today: {fmt_int_grouped(totals['queries'])}",
        f"📋 Blocklist domains: {fmt_int_grouped(totals['rules'])}",
        f"👥 Active clients: {fmt_int_grouped(totals['clients'])}",
    ]
    lines.extend(extra_lines or [])
    top = totals["top"]
    if top:
        lines.append(f"🔝 Top blocked: {top.get('name')} "
                     f"({fmt_int_grouped(top.get('count'))})")
    lines.append(f"🔐 {protection_label}: {'ON' if on == n else f'{on}/{n} ON'}")
    if failed:
        lines.append(f"⚠️ unreachable: {', '.join(failed)}")
    return {"ok": True, "detail": "\n".join(lines), "status": 200}


async def fleet_blocker_status(insts: list, fetch_fn, *, app_label: str,
                               header: str, protection_label: str,
                               extra_lines_fn=None) -> dict:
    """The full read-only DNS-blocker fleet status skill, shared by every
    fleet blocker module's ``_skill_status``: guard empty instances,
    parallel force-fetch every host, aggregate via ``fleet_blocker_totals``,
    render via ``fleet_blocker_detail``. ``app_label`` fills the empty /
    all-unreachable messages; ``header`` / ``protection_label`` the detail
    lines. ``extra_lines_fn(ok_rows, totals) -> list[str]`` (optional)
    supplies app-specific stat lines (AdGuard's query-weighted
    avg-processing-ms). Never raises — a single down host is footnoted, an
    all-down fleet returns ``ok=False``."""
    if not insts:
        return {"ok": False, "detail": f"no {app_label} instances configured",
                "status": 0}
    ok_rows, failed = await fleet_fetch_all(insts, fetch_fn)
    if not ok_rows:
        return {"ok": False, "detail": f"all {app_label} hosts unreachable",
                "status": 0}
    totals = fleet_blocker_totals(ok_rows)
    extra = extra_lines_fn(ok_rows, totals) if extra_lines_fn else None
    return fleet_blocker_detail(totals, failed, header=header,
                                protection_label=protection_label,
                                extra_lines=extra)


def fleet_instances(slugs: "tuple[str, ...]") -> list:
    """Enumerate every pinned instance of a FLEET app across the curated
    host list: ``[(host_id, service_idx, host_row, chip)]``, deduped by
    ``(host_id, service_idx)`` in case aliases overlap. Shared by every
    fleet module (AdGuard / Pi-hole / future N-host apps) whose
    ``run_skill`` fans out across all instances — pass the module's own
    ``SLUGS`` tuple. Never raises (returns ``[]`` if the registry import
    fails)."""
    # noinspection PyBroadException
    try:
        from logic.apps.registry import instances_for_slug  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    out = []
    for slug in slugs:
        for tup in instances_for_slug(slug):
            out.append(tup)
    seen = set()
    uniq = []
    for hid, sidx, hrow, chip in out:
        k = (hid, sidx)
        if k in seen:
            continue
        seen.add(k)
        uniq.append((hid, sidx, hrow, chip))
    return uniq


def peek_cache(cache: dict, host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call, no TTL check) for a per-app
    module's ``peek_latest`` — returns the last fetched per-host stats
    for ``(host_id, service_idx)`` or ``None``. The AI context's
    ``app_skills[].last`` is the canonical consumer (it must stay
    side-effect-free, so this never fetches)."""
    cached = cache.get(cache_key(host_id, service_idx))
    if cached is None:
        return None
    # Tuple-unpack rather than `cached[1]` — the cache value is a
    # ``(stored_at, value)`` pair; subscripting an ``Any | None`` makes the
    # type checker flag a phantom None.__getitem__ even after the is-None
    # guard (Any absorbs None so `is None` doesn't narrow it).
    _stored_at, value = cached
    return value


def fmt_int_grouped(v) -> str:
    """Format an integer with thousands separators; ``"—"`` for a
    non-numeric / missing value. Shared by the fleet modules' aggregate
    detail blocks (web inline + Telegram + AI)."""
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return "—"


def resolve_base_url(host_row: dict, chip: dict) -> str:
    """Resolve an app chip's upstream base URL.

    Priority: the chip's own ``url`` field (operator-set; includes
    scheme + optional port) wins; else
    ``<proto>://<host.address>:<first http/https probe port>``. Returns
    the URL with trailing slashes stripped so the caller appends its API
    path directly. Empty string when nothing resolves.
    """
    url = (chip.get("url") or "").strip()
    if url:
        return url.rstrip("/")
    address = (host_row.get("address") or "").strip()
    if not address:
        return ""
    probe = chip.get("probe") or {}
    ports = probe.get("ports") or []
    if isinstance(ports, list):
        for p in ports:
            if not isinstance(p, dict):
                continue
            port_n = p.get("port")
            proto = (p.get("protocol") or "").strip().lower()
            if isinstance(port_n, int) and 1 <= port_n <= 65535 and proto in ("http", "https"):
                return f"{proto}://{address}:{port_n}".rstrip("/")
    return ""


def cache_key(host_id: str, service_idx: int) -> str:
    """Canonical per-(host, service) cache key for a per-app data cache."""
    return f"{host_id}:{service_idx}"


def resolve_credential_target(host_row: dict, chip: dict,
                              candidate_key: str) -> "tuple[str, str, Optional[dict]]":
    """Resolve ``(api_key, base_url)`` for a per-app ``test_credential`` probe.

    Applies the standard blank-candidate -> stored-``chip['api_key']``
    fallback so the operator can re-test after first save without retyping
    the secret. Returns ``(key, base, err)`` where ``err`` is a
    ready-to-return ``{ok: False, detail, status}`` dict when the key or
    URL is missing (``key`` / ``base`` are ``""`` in that case), else
    ``None``. Folds the identical opening every credentialed module's
    ``test_credential`` repeated."""
    key = (candidate_key or "").strip() or (chip.get("api_key") or "").strip()
    if not key:
        return "", "", {"ok": False, "detail": "api_key required", "status": 0}
    base = resolve_base_url(host_row, chip)
    if not base:
        return key, "", {"ok": False, "detail": "no upstream URL configured",
                         "status": 0}
    return key, base, None


# Per-instance data-cache TTL bounds. The TTL is operator-configurable IN
# THE APP (the per-instance editor's optional `cache_ttl` field) — NOT a
# global Config TUNABLE, so each app stays self-contained. Each module
# passes its OWN default (e.g. AdGuard / Pi-hole 30s, Speedtest 60s).
_CACHE_TTL_MIN = 5
_CACHE_TTL_MAX = 3600


def resolve_cache_ttl(chip: dict, default_ttl: int) -> int:
    """Resolve a chip's per-instance data-cache TTL (seconds): the operator-
    set ``chip['cache_ttl']`` clamped to ``[5, 3600]``, or ``default_ttl``
    (the app's own default) when unset / blank / unparseable. Read per-use
    inside ``fetch_data`` (NOT cached at import) so an editor change takes
    effect on the next fetch."""
    n = safe_int((chip or {}).get("cache_ttl"), default_ttl)
    return max(_CACHE_TTL_MIN, min(_CACHE_TTL_MAX, n))


def cache_get(cache: dict, key: str, ttl_s: float, now: float,
              force: bool = False) -> Optional[dict]:
    """Return the cached value for ``key`` when it's younger than
    ``ttl_s`` seconds (and ``force`` is false), else ``None``. ``cache``
    maps ``key -> (stored_at_epoch, value)``."""
    if force:
        return None
    cached = cache.get(key)
    if cached is None:
        return None
    stored_at, value = cached
    return value if (now - stored_at) < ttl_s else None


def fetch_preamble(host_row: dict, chip: dict, host_id: str, service_idx: int,
                   cache: dict, ttl_s: float, now: float,
                   force: bool) -> "tuple[str, Optional[dict]]":
    """The shared ``fetch_data`` preamble for credentialed per-app modules:
    resolve the chip's base URL (raise ``ValueError`` when it won't
    resolve) and return any still-fresh cached value. Returns
    ``(base_url, cached_or_None)`` — the caller returns the cached value
    immediately when it's non-None, else proceeds with ``base_url``. The
    per-app CREDENTIAL check stays in each module (it differs per app)."""
    base = resolve_base_url(host_row, chip)
    if not base:
        raise ValueError("no upstream URL configured for this instance")
    return base, cache_get(cache, cache_key(host_id, service_idx), ttl_s, now, force)


def fetch_gate(host_row: dict, chip: dict, host_id: str, service_idx: int,
               cache: dict, ttl_s: float, now: float, force: bool, *,
               credential, log_tag: str) -> "tuple[str, Optional[dict]]":
    """Credential + URL + cache gate for a fleet module's ``fetch_data``.
    Raises ``ValueError`` when ``credential`` is falsy (the per-app secret
    isn't set) or the URL won't resolve; otherwise returns
    ``(base_url, cached_or_None)`` like ``fetch_preamble`` and logs a
    ``[<log_tag>] INFO fetch …`` line on a cache MISS (so a served-from-
    cache hit stays quiet). The caller still does ``if hit is not None:
    return hit`` — that early-return is its own control flow. Folds the
    duplicated credential-check + preamble + miss-log shape that every
    fleet module's ``fetch_data`` opened with."""
    if not credential:
        raise ValueError("credential not set for this instance")
    base, hit = fetch_preamble(host_row, chip, host_id, service_idx,
                               cache, ttl_s, now, force)
    if hit is None:
        print(f"[{log_tag}] INFO fetch host={host_id} svc_idx={service_idx} url={base}")
    return base, hit


async def fleet_fan_out(insts: list, one_fn, *, app_label: str, verb: str,
                        log_tag: str, log_extra: str = "") -> dict:
    """Run a fleet action across every instance: guard empty, fan
    ``one_fn(*inst_tuple)`` in parallel, tally ok/fail via
    ``fleet_action_result``, log a ``[<log_tag>] INFO fleet …`` line, return
    the ``{ok, detail, status}`` envelope. ``one_fn`` is the module's
    per-host action closure ``(hid, sidx, hrow, chip) -> (hid, ok, err)``
    (it owns the app-specific auth + endpoint calls); ``verb`` is the
    past-tense action word for the detail line. Folds the identical
    guard / gather / tally / log shell every fleet module's
    ``_skill_fleet_action`` wrapped around its ``_one``."""
    if not insts:
        return {"ok": False, "detail": f"no {app_label} instances configured",
                "status": 0}
    results = await asyncio.gather(*[one_fn(*t) for t in insts])
    out = fleet_action_result(results, app_label, verb)
    _extra = (" " + log_extra) if log_extra else ""
    print(f"[{log_tag}] INFO fleet{_extra} -> {out.get('detail')}")
    return out


async def fleet_blocker_action(insts: list, one_fn, *, action: str, seconds: int,
                               app_label: str, log_tag: str,
                               refresh_verb: str) -> dict:
    """``_skill_fleet_action`` shell for a fleet DNS-blocker: derive the
    past-tense ``verb`` from ``action`` (``enable``→enabled / ``disable``→
    disabled / ``refresh``→``refresh_verb``; a timed disable becomes
    ``disabled for Ns``), then delegate to ``fleet_fan_out`` with the
    standard ``action=… seconds=…`` log_extra. ``one_fn`` is the module's
    app-specific per-host closure (the auth model — SID vs Basic — is the
    only real divergence between fleet blockers); ``refresh_verb`` is the
    one word that differs ("gravity updated" vs "refreshed")."""
    verb = {"enable": "enabled", "disable": "disabled",
            "refresh": refresh_verb}.get(action, action)
    if action == "disable" and seconds > 0:
        verb = f"disabled for {seconds}s"
    return await fleet_fan_out(insts, one_fn, app_label=app_label, verb=verb,
                               log_tag=log_tag,
                               log_extra=f"action={action} seconds={seconds}")


async def fleet_run_skill(skill_id: str, *, prefix: str, status_fn, action_fn,
                          skills) -> dict:
    """The shared ``run_skill`` dispatch ladder for a fleet DNS-blocker.
    Maps ``<prefix>_status`` → ``status_fn()`` (read-only); ``<prefix>_enable``
    / ``<prefix>_reenable`` → ``action_fn("enable")``; ``<prefix>_disable`` →
    ``action_fn("disable")`` (indefinite); ``<prefix>_refresh`` →
    ``action_fn("refresh")``; ``<prefix>_disable_<dur>`` →
    ``action_fn("disable", <disable_seconds from skills>)``. ``action_fn``
    takes SECONDS as its optional second arg (the natural unit — a module
    whose upstream wants milliseconds converts internally). Raises
    ``ValueError`` on an unknown skill id (the route maps it to HTTP 404)."""
    if skill_id == f"{prefix}_status":
        return await status_fn()
    if skill_id in (f"{prefix}_enable", f"{prefix}_reenable"):
        return await action_fn("enable")
    if skill_id == f"{prefix}_disable":
        return await action_fn("disable")
    if skill_id == f"{prefix}_refresh":
        return await action_fn("refresh")
    if skill_id.startswith(f"{prefix}_disable_"):
        secs = next((safe_int(s.get("disable_seconds")) for s in skills
                     if s.get("id") == skill_id), 0)
        if not secs:
            raise ValueError(f"unknown disable preset: {skill_id!r}")
        return await action_fn("disable", secs)
    raise ValueError(f"unknown skill: {skill_id!r}")
