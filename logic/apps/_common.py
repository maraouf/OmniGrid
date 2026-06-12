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


def chip_is_docker_linked(chip: dict) -> bool:
    """True when an app chip is linked to a Portainer container / stack (the
    operator set ``docker_container`` or ``docker_stack`` via the editor's
    "Link to Docker").

    Single source of truth for "this app's updates flow through the inline
    Docker Restart / Update actions" — consumed by the registry's skill gate
    and the AI / Telegram context filter so the manual-update skills
    (version-check + built-in-updater) are hidden / refused for Docker-linked
    instances. Mirrors the SPA's ``inst.docker_container || inst.docker_stack``
    check. Defensive against a non-dict chip."""
    if not isinstance(chip, dict):
        return False
    return bool((chip.get("docker_container") or "").strip()
                or (chip.get("docker_stack") or "").strip())


# ---------------------------------------------------------------------------
# Per-app sampler scaffolding — shared by every logic/apps/<slug>_sampler.py
# (flaresolverr / ddns / speedtest / …) so the instance-enum + interval-resolve
# boilerplate lives in ONE place instead of being copy-pasted per sampler.
# ---------------------------------------------------------------------------
def sampler_instances(slug: str, log_tag: str) -> list:
    """Configured chips for a per-app sampler as ``[(host_id, service_idx,
    host_row, chip)]``. ``[]`` on any failure (the sampler stays dormant). Lazy
    registry import avoids an import cycle at module load."""
    try:
        from logic.apps import registry as _registry  # noqa: PLC0415
        return _registry.instances_for_slug(slug)
    except Exception as e:  # noqa: BLE001
        print(f"[{log_tag}] instance enum failed: {e}")
        return []


def disk_runway_days(free_series: list, *, cap_days: int = 3650) -> Optional[int]:
    """Project days-until-disk-full from a FREE-space series (oldest-first GiB
    values, one per day) via an ordinary-least-squares linear fit on the
    day-index.

    Returns the projected days from the LATEST sample until free space reaches 0
    when the trend is DECLINING (slope < 0) AND there are >= 3 points; ``None``
    when free space is flat / growing or there are too few points. Capped at
    ``cap_days`` (~10 years) so a near-flat decline can't render an absurd
    number. Shared by the qBittorrent + *arr retention samplers (both surface a
    'disk full in ~N days at this fill rate' projection)."""
    pts = [float(v) for v in (free_series or [])]
    n = len(pts)
    if n < 3:
        return None
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(pts) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((xs[i] - mean_x) * (pts[i] - mean_y) for i in range(n))
    slope = sxy / sxx  # GiB of free space gained per day (negative = filling)
    if slope >= 0:
        return None
    latest = pts[-1]
    if latest <= 0:
        return 0
    days = latest / (-slope)
    return 0 if days <= 0 else int(min(days, cap_days))


def resolve_sample_interval(interval_tunable) -> int:
    """Resolve a per-app sampler's tick cadence (seconds): the app's dedicated
    interval tunable, or — when it resolves to 0 — the global stats sample
    interval (floored at 60s). ``interval_tunable`` is a ``Tunable`` member."""
    from logic import tuning as _tuning  # noqa: PLC0415
    from logic.tuning import Tunable as _Tunable  # noqa: PLC0415
    iv = _tuning.tuning_int(interval_tunable)
    if iv and iv > 0:
        return iv
    return max(60, _tuning.tuning_int(_Tunable.STATS_SAMPLE_INTERVAL_SECONDS))


async def run_sampler_tick(tick: int, *, instances_fn, probe_fn, interval_fn,
                           log_tag: str, prune_table: str,
                           history_days_tunable) -> None:
    """Generic per-app sampler tick body: probe every configured instance in
    parallel, then — once an hour — prune ``<prune_table>`` to the retention
    window. Shared by every ``<slug>_sampler._tick`` (the probe / interval
    callbacks + the table + retention tunable are the only differences), so the
    gather + prune-cadence boilerplate lives in ONE place. ``probe_fn`` is the
    sampler's ``_probe_one(host_id, service_idx, host_row, chip)`` coroutine."""
    import asyncio as _asyncio  # noqa: PLC0415
    instances = instances_fn()
    if instances:
        await _asyncio.gather(
            *(probe_fn(*t) for t in instances), return_exceptions=True)
    interval = interval_fn()
    if tick % max(1, 3600 // max(1, interval)) == 0:
        import time as _time  # noqa: PLC0415
        from logic import tuning as _tuning  # noqa: PLC0415
        from logic.db import prune_rows_older_than  # noqa: PLC0415
        from logic.sampler_metrics import prune_with_metrics  # noqa: PLC0415
        days = _tuning.tuning_int(history_days_tunable)
        cutoff = int(_time.time()) - days * 86400
        table = _safe_table(prune_table)

        def _prune() -> int:
            return prune_rows_older_than(table, cutoff)

        n = await prune_with_metrics(log_tag, _prune)
        if n:
            print(f"[{log_tag}] pruned {n} rows older than {days}d")


# ---------------------------------------------------------------------------
# Fleet DNS-blocker sampler scaffolding — shared by adguardhome_sampler.py +
# pihole_sampler.py (both snapshot the SAME queries/blocked/clients shape into
# their own <slug>_samples table + derive the SAME fleet blocked-% trend), so
# the probe-write + trend math live in ONE place.
# ---------------------------------------------------------------------------
def _safe_table(table: str) -> str:
    """Guard a sampler table name before f-stringing it into SQL — it's always
    a hardcoded module constant, never user input, but validate anyway."""
    if not table.replace("_", "").isalnum():
        raise ValueError(f"unsafe table name: {table!r}")
    return table


async def probe_blocker_sample(fetch_fn, table: str, host_id: str,
                               service_idx: int, host_row: dict, chip: dict,
                               log_tag: str) -> None:
    """Fetch one fleet-blocker host via ``fetch_fn(force=True)`` and snapshot
    its queries / blocked / clients counters into ``<table>`` (one row per
    tick). A host that's down / unreachable skips the write (no phantom 0 row);
    a code bug also skips. Shared by the AdGuard + Pi-hole samplers — only the
    fetch_fn + table + log_tag differ."""
    import asyncio as _asyncio  # noqa: PLC0415
    import time as _time  # noqa: PLC0415
    from logic.db import db_conn  # noqa: PLC0415
    try:
        data = await fetch_fn(host_row, chip, host_id=host_id,
                              service_idx=int(service_idx), force=True)
    except (_asyncio.CancelledError, KeyboardInterrupt):
        raise
    except (ValueError, RuntimeError) as e:
        print(f"[{log_tag}] probe {host_id}#{service_idx} down: {e}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"[{log_tag}] probe {host_id}#{service_idx} error: {type(e).__name__}: {e}")
        return
    if not isinstance(data, dict) or not data.get("ok"):
        return
    row = (int(_time.time()), host_id, int(service_idx),
           int(data.get("queries_today") or 0), int(data.get("blocked_today") or 0),
           int(data.get("num_clients") or 0))
    try:
        with db_conn() as c:
            c.execute(
                f"INSERT OR REPLACE INTO {_safe_table(table)} "
                "(ts, host_id, service_idx, queries, blocked, clients) "
                "VALUES (?,?,?,?,?,?)", row)
    except Exception as e:  # noqa: BLE001
        print(f"[{log_tag}] write {host_id}#{service_idx} failed: {e}")


def fleet_blocker_trend_summary(table: str, days_tunable, days: int = 0, *,
                                max_points: int = 90) -> dict:
    """Fleet-wide daily blocked-% trend from a ``<table>`` of queries/blocked
    snapshots. Returns ``{days, samples, median_pct, latest_pct, series}`` where
    ``series`` is up to ``max_points`` daily blocked-% points (oldest-first,
    days WITH data only) computed by taking each host's daily-MAX queries+blocked
    (the cumulative today-counter peaks just before the daily reset ≈ that day's
    total), summing across the fleet per day, then ``blocked/queries*100``.
    Zeroed shape when no samples yet — never raises. Shared by the AdGuard +
    Pi-hole samplers; only the table + retention tunable differ."""
    import time as _time  # noqa: PLC0415
    from collections import defaultdict as _defaultdict  # noqa: PLC0415
    from logic import tuning as _tuning  # noqa: PLC0415
    from logic.db import db_conn  # noqa: PLC0415
    win = int(days) if days else _tuning.tuning_int(days_tunable)
    out: dict = {"days": int(win), "samples": 0, "median_pct": 0.0,
                 "latest_pct": 0.0, "series": []}
    cutoff = int(_time.time()) - int(win) * 86400
    try:
        with db_conn() as c:
            rows = c.execute(
                f"SELECT ts, host_id, service_idx, queries, blocked "
                f"FROM {_safe_table(table)} WHERE ts >= ? ORDER BY ts ASC",
                (cutoff,),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[{table}] trend_summary failed: {e}")
        return out
    if not rows:
        return out
    out["samples"] = len(rows)
    day_host_q: dict = _defaultdict(int)
    day_host_b: dict = _defaultdict(int)
    for r in rows:
        key = (int(r["ts"]) // 86400, str(r["host_id"]), int(r["service_idx"]))
        q = int(r["queries"] or 0)
        b = int(r["blocked"] or 0)
        if q > day_host_q[key]:
            day_host_q[key] = q
        if b > day_host_b[key]:
            day_host_b[key] = b
    day_q: dict = _defaultdict(int)
    day_b: dict = _defaultdict(int)
    for (day, _h, _s), q in day_host_q.items():
        day_q[day] += q
    for (day, _h, _s), b in day_host_b.items():
        day_b[day] += b
    series = []
    for day in sorted(day_q):
        q = day_q[day]
        series.append(round((day_b[day] / q) * 100.0, 2) if q > 0 else 0.0)
    if not series:
        return out
    if len(series) > max_points:
        stride = len(series) / float(max_points)
        series = [series[int(i * stride)] for i in range(max_points)]
    out["series"] = series
    out["latest_pct"] = series[-1]
    srt = sorted(series)
    mid = len(srt) // 2
    out["median_pct"] = srt[mid] if len(srt) % 2 else round((srt[mid - 1] + srt[mid]) / 2.0, 2)
    return out


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


def resolve_userpass(chip: dict, *, password: "Optional[str]" = None,
                     username: "Optional[str]" = None) -> "tuple[str, str]":
    """Resolve ``(username, password)`` for a two-field-credential app (the
    plain ``username`` chip field + the secret ``api_key`` chip field).

    Explicit args win (a pre-save test passes the candidate values); else fall
    back to the stored chip fields. Shared by every username+password app
    (AdGuard Home / qBittorrent / Nginx Proxy Manager — the latter's "email"
    IS the username field), folding the identical per-module ``_creds`` helper
    into one place."""
    u = (username if username is not None else "").strip() or (chip.get("username") or "").strip()
    p = (password if password is not None else "").strip() or (chip.get("api_key") or "").strip()
    return u, p


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
