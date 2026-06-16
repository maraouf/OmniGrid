"""ddns-updater per-app module (qdm12/ddns-updater).

Encapsulates everything ddns-updater-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic.

What this is
------------
ddns-updater keeps a set of DNS records pointed at the host's current
public IP across many providers. Its server exposes NO JSON API — only:

    GET  /        — the HTML web UI (a table of records + status)
    GET  /update  — trigger an update of every record now (no auth)
    GET  /static/* — assets

So this module PARSES the web UI's HTML table. Each cell carries a
``data-label`` attribute (Domain / Owner / Provider / IP Version /
Update Status / Current IP / Previous IPs), which makes the scrape
reliable. The record ``Status`` is one of ddns-updater's constants:
``success`` / ``up to date`` (healthy), ``failure`` (failing),
``updating`` / ``unset`` (transient). There is NO authentication on the
server, so ``requires_api_key()`` is False — the editor only needs the
instance URL (set in the generic chip URL field) + a cache TTL.

The expanded card answers "are my DNS records in sync, and what's my
public IP" at a glance:

    records_total  — number of records ddns-updater manages
    up_count       — records that are success / up to date
    fail_count     — records in failure
    public_ip      — the current public IP (from a record's Current IP)
    failing_domains— up to 8 domains currently failing

AI / Telegram skills
--------------------
* ``ddns_status``  — list the records + their status + the public IP.
* ``ddns_update``  — trigger an update of every record now (GET /update).

Single-instance app (NOT fleet) — one card per pinned chip.

Status constants (internal/constants/status.go):
    failure / success / up to date / updating / unset
"""
from __future__ import annotations

import html as _html
import re
import time
from typing import Any, Optional

import httpx

from logic.apps._common import (
    cache_key, fetch_preamble, peek_cache, resolve_base_url, resolve_cache_ttl)
from logic.coerce import as_list, safe_int

# Catalog template slug.
SLUGS: tuple[str, ...] = ("ddns-updater",)

SKILLS: tuple[dict, ...] = (
    {
        "id": "ddns_status",
        "name": "DNS records status",
        "ai_phrases": ("ddns status, dns updater status, are my dns records up "
                       "to date, ddns-updater records, dynamic dns status, "
                       "what's my public ip, is my ddns working, failing dns records"),
        "destructive": False,
    },
    {
        "id": "ddns_records",
        "name": "List DNS records",
        "ai_phrases": ("list my dns records, show ddns records, what dns records "
                       "do i have, ddns record list, show all my dynamic dns "
                       "entries, dns updater record list, what domains does ddns "
                       "manage"),
        "destructive": False,
    },
    {
        "id": "ddns_update",
        "name": "Update DNS now",
        "ai_phrases": ("update dns now, run ddns update, trigger ddns update, "
                       "refresh dns records, force a dns update, update my dns, "
                       "push public ip to dns"),
        "destructive": False,
    },
)

DEFAULT_CACHE_TTL_S = 60
_data_cache: dict[str, tuple[float, dict]] = {}

# Each record renders as one <tr> with <td data-label="...">value</td> cells.
_ROW_RE = re.compile(r"<tr[^>]*>(?P<body>.*?)</tr>", re.S | re.I)
_CELL_RE = re.compile(r'<td[^>]*\bdata-label="(?P<label>[^"]+)"[^>]*>(?P<value>.*?)</td>', re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_IP_RE = re.compile(r"\b(?:\d{1,3}(?:\.\d{1,3}){3}|[0-9A-Fa-f:]{2,}:[0-9A-Fa-f:]+)\b")
# ddns-updater appends a relative time to the status cell ("up to date,
# 5 minutes ago" / "success, just now"). This pulls that suffix out so the
# bare status word stays clean (for _classify) AND the "last updated" hint is
# surfaced separately.
_RELTIME_RE = re.compile(
    r"\b(?:just now|\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago)\b",
    re.I)


def requires_api_key() -> bool:
    """False — ddns-updater's web UI / update endpoint have NO auth; the
    editor only needs the instance URL + a cache TTL."""
    return False


def _clean_cell(raw: str) -> str:
    """Strip inner HTML tags + unescape entities from a table cell."""
    return _html.unescape(_TAG_RE.sub("", raw or "")).strip()


def _classify(status: Any) -> str:
    """Map a ddns-updater record status to ok / fail / pending.

    SUBSTRING match (not exact): ddns-updater renders the status word with a
    trailing relative-time / timestamp (``up to date, 5 minutes ago``) and
    sometimes wraps it in a coloured ``<span>``, so after tag-stripping the
    cell text carries more than the bare constant. Fail-first ordering so
    ``failure`` (which contains no ok token) wins before the ok check; the
    transient states (``updating`` / ``unset``) match neither and fall to
    pending."""
    s = str(status or "").strip().lower()
    if not s:
        return "pending"
    if "fail" in s or "error" in s:
        return "fail"
    if "up to date" in s or "success" in s:
        return "ok"
    return "pending"  # updating / unset / unknown


def _split_status(raw: Any) -> tuple[str, str]:
    """Split a ddns-updater status cell into ``(status_word, last_updated)``.

    The cell text carries the bare constant plus a trailing relative time
    (``up to date, 5 minutes ago``). This returns the status portion (with the
    relative time + a trailing comma/dash stripped) and the relative time on
    its own (``"5 minutes ago"`` / ``"just now"`` / ``""`` when absent), so the
    card can show "last updated Xm ago" without polluting the status word that
    ``_classify`` reads."""
    s = str(raw or "").strip()
    if not s:
        return "", ""
    m = _RELTIME_RE.search(s)
    if not m:
        return s, ""
    last = m.group()
    status = s[:m.start()].rstrip(" ,;–-").strip()
    if not status:  # relative time came first — take whatever trails it
        status = s[m.end():].strip(" ,;–-").strip()
    return status, last


# Maps a ddns-updater relative-time suffix ("5 minutes ago" / "just now") to an
# age in SECONDS so the card can flag records that haven't re-pushed recently.
_RELTIME_UNIT_S = {"second": 1, "minute": 60, "hour": 3600, "day": 86400,
                   "week": 604800, "month": 2592000, "year": 31536000}
_RELTIME_PARSE_RE = re.compile(
    r"(?P<n>\d+)\s+(?P<unit>second|minute|hour|day|week|month|year)s?\s+ago", re.I)


def _reltime_to_seconds(rel: Any) -> Optional[int]:
    """Parse a ddns-updater relative-time string into an age in seconds.
    ``"just now"`` → 0; ``"5 minutes ago"`` → 300; an empty / unparseable string
    → None (unknown age — NOT treated as stale)."""
    s = str(rel or "").strip().lower()
    if not s:
        return None
    if "just now" in s:
        return 0
    m = _RELTIME_PARSE_RE.search(s)
    if not m:
        return None
    return int(m.group("n")) * _RELTIME_UNIT_S[m.group("unit").lower()]


def _parse_records(html_text: Any) -> list[dict]:
    """Parse the ddns-updater web-UI HTML into a list of record dicts
    ``{domain, owner, provider, ip_version, status, last_updated, current_ip,
    previous_ips}``. Defensive over malformed HTML (returns ``[]``). Keys on the
    per-cell ``data-label`` attribute, so column reordering doesn't break it.

    The status cell's trailing relative time is split off into ``last_updated``
    (``_split_status``) and the "Previous IPs" column — present in the HTML but
    previously dropped — is captured as ``previous_ips`` so the card can show
    the prior IP per domain."""
    if not isinstance(html_text, str) or not html_text:
        return []
    out: list[dict] = []
    for row in _ROW_RE.finditer(html_text):
        cells = {k.strip().lower(): _clean_cell(v)
                 for k, v in _CELL_RE.findall(row.group(1))}
        status_raw = (cells.get("update status") or cells.get("status")
                      or cells.get("update") or "")
        if not cells or ("domain" not in cells and not status_raw):
            continue
        status_word, last_updated = _split_status(status_raw)
        out.append({
            "domain": cells.get("domain", ""),
            "owner": cells.get("owner", ""),
            "provider": cells.get("provider", ""),
            "ip_version": cells.get("ip version", ""),
            "status": status_word or status_raw,
            "last_updated": last_updated,
            "current_ip": cells.get("current ip", ""),
            "previous_ips": cells.get("previous ips", ""),
        })
    return out


def _shape(records: list[dict]) -> dict:
    """Roll records into the card shape: totals + the public IP + failing
    domains."""
    total = len(records)
    up = sum(1 for r in records if _classify(r.get("status")) == "ok")
    fail = sum(1 for r in records if _classify(r.get("status")) == "fail")
    failing = [r.get("domain") or r.get("owner") or "?"
               for r in records if _classify(r.get("status")) == "fail"]
    # "Stale" records: an OK (success / up-to-date) record whose last successful
    # update is older than the operator-tunable threshold — a record that thinks
    # it's fine but silently stopped re-pushing (distinct from a failing record,
    # which is already counted above). Age comes from the scraped relative-time
    # suffix; a record with no parseable age is NOT counted.
    from logic.tuning import tuning_int, Tunable  # noqa: PLC0415
    stale_after_s = max(1, tuning_int(Tunable.DDNS_STALE_RECORD_HOURS)) * 3600
    stale = 0
    for r in records:
        if _classify(r.get("status")) != "ok":
            continue
        age = _reltime_to_seconds(r.get("last_updated"))
        if age is not None and age > stale_after_s:
            stale += 1
    # IPv4 vs IPv6 record split — ddns-updater's "IP Version" cell is "ipv4" /
    # "ipv6" (a dual "ipv4 or ipv6" carries both digits → counts toward both).
    ipv4 = sum(1 for r in records if "4" in str(r.get("ip_version") or "").lower())
    ipv6 = sum(1 for r in records if "6" in str(r.get("ip_version") or "").lower())
    # Provider breakdown — count per DNS provider, busiest-first (already parsed
    # per record).
    prov_counts: dict[str, int] = {}
    for r in records:
        prov = str(r.get("provider") or "").strip()
        if prov:
            prov_counts[prov] = prov_counts.get(prov, 0) + 1
    provider_breakdown = [{"provider": p, "count": n} for p, n in
                          sorted(prov_counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    public_ip = ""
    for r in records:
        m = _IP_RE.search(str(r.get("current_ip") or ""))
        if m:
            public_ip = m.group()
            break
    # Compact per-record list for the expanded card + AI ("which domain on
    # which provider, last updated when"). Capped so a huge record set can't
    # bloat the payload; ``status`` is the classified bucket, ``status_raw``
    # the human label.
    compact = [{
        "domain": r.get("domain", ""),
        "provider": r.get("provider", ""),
        "ip_version": r.get("ip_version", ""),
        "status": _classify(r.get("status")),
        "status_raw": r.get("status", ""),
        "last_updated": r.get("last_updated", ""),
        "current_ip": r.get("current_ip", ""),
        "previous_ips": r.get("previous_ips", ""),
    } for r in records[:50]]
    return {
        "records_total": total,
        "up_count": up,
        "fail_count": fail,
        "stale_count": stale,
        "stale_after_hours": stale_after_s // 3600,
        "ipv4_count": ipv4,
        "ipv6_count": ipv6,
        "provider_breakdown": provider_breakdown,
        "public_ip": public_ip,
        "failing_domains": failing[:8],
        "records": compact,
    }


# noinspection PyUnusedLocal
async def test_credential(host_row: dict, chip: dict, candidate_key: str, *,
                          payload: Optional[dict] = None, **_kw) -> dict:
    """Probe the ddns-updater web UI (``GET /``) and confirm it parses into
    records. No auth — ``candidate_key`` / ``payload`` are part of the generic
    route contract but unused here (ddns-updater has no credentials). Returns
    ``{ok, detail, status}``."""
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "detail": "no upstream URL configured", "status": 0}
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/")
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if r.status_code != 200:
        return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}
    try:
        records = _parse_records(r.text)
    except (ValueError, TypeError):
        records = []
    n = len(records)
    return {"ok": True, "detail": f"OK ({n} record{'s' if n != 1 else ''})",
            "status": 200}


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Fetch + parse the ddns-updater web UI for the expanded card.

    Returns ``{available, records_total, up_count, fail_count, public_ip,
    failing_domains, fetched_at}``. Raises ``ValueError`` (base URL won't
    resolve) / ``RuntimeError`` (upstream error)."""
    now = time.time()
    base, hit = fetch_preamble(host_row, chip, host_id, service_idx, _data_cache,
                               resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force)
    if hit is not None:
        return hit
    url = base + "/"
    print(f"[ddns] INFO fetch host={host_id} svc_idx={service_idx} url={url}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[ddns] error: fetch host={host_id} url={url} failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    if r.status_code != 200:
        print(f"[ddns] error: fetch host={host_id} url={url} returned HTTP "
              f"{r.status_code} (check the chip URL points at the ddns-updater "
              f"web UI root)")
        raise RuntimeError(f"upstream returned HTTP {r.status_code} for {url}")
    try:
        records = _parse_records(r.text)
    except (ValueError, TypeError):  # noqa: BLE001
        records = []
    shaped = _shape(records)
    out: dict[str, Any] = {"available": True, "fetched_at": int(now), **shaped}
    # Embed the public-IP-change timeline + fail-count sparkline from the
    # lifespan sampler (best-effort — a sampler/DB hiccup must not fail the
    # card). Empty/zeroed shape until the first samples accrue.
    try:
        from logic.apps import ddns_updater_sampler as _ddns_sampler  # noqa: PLC0415
        out["history"] = _ddns_sampler.history_summary(host_id, int(service_idx))
    except Exception as e:  # noqa: BLE001
        print(f"[ddns] warning: history_summary({host_id}#{service_idx}) failed: {e}")
    _raw_statuses = sorted({str(r.get("status") or "").strip() for r in records} - {""})
    print(f"[ddns] INFO fetched host={host_id} records={out['records_total']} "
          f"up={out['up_count']} fail={out['fail_count']} ip={out['public_ip']} "
          f"raw_statuses={_raw_statuses}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "records_total": safe_int(data.get("records_total")),
        "up_count": safe_int(data.get("up_count")),
        "fail_count": safe_int(data.get("fail_count")),
        "stale_count": safe_int(data.get("stale_count")),
        "public_ip": data.get("public_ip") or "",
        "fetched_at": safe_int(data.get("fetched_at")),
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Raises ValueError on an unknown id."""
    if skill_id == "ddns_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    if skill_id == "ddns_records":
        return await _records_skill(host_row, chip, host_id=host_id,
                                    service_idx=service_idx)
    if skill_id == "ddns_update":
        return await _update_skill(host_row, chip, host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


_STATUS_EMOJI = {"ok": "✅", "fail": "❌", "pending": "⏳"}


# noinspection DuplicatedCode
async def _records_skill(host_row: dict, chip: dict, *,
                         host_id: Optional[str] = None,
                         service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch + list every record with its provider, status and
    last-updated time. Never raises."""
    print(f"[ddns] INFO ddns_records host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[ddns] warning: ddns_records host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    records = as_list(data.get("records"))
    if not records:
        return {"ok": True, "status": 200,
                "detail": "No DNS records found in ddns-updater."}
    lines: list[str] = []
    for r in records[:30]:
        if not isinstance(r, dict):
            continue
        emoji = _STATUS_EMOJI.get(str(r.get("status") or ""), "•")
        domain = str(r.get("domain") or "?").strip()
        provider = str(r.get("provider") or "").strip()
        ipv = str(r.get("ip_version") or "").strip()
        when = str(r.get("last_updated") or "").strip()
        ip = str(r.get("current_ip") or "").strip()
        bits = [f"{emoji} {domain}"]
        meta = " · ".join(b for b in (provider, ipv) if b)
        if meta:
            bits.append(f"({meta})")
        if ip:
            bits.append(f"→ {ip}")
        if when:
            bits.append(f"· {when}")
        lines.append(" ".join(bits))
    extra = len(records) - 30
    if extra > 0:
        lines.append(f"…and {extra} more")
    public_ip = str(data.get("public_ip") or "").strip()
    head = f"🌐 {len(records)} DNS record{'s' if len(records) != 1 else ''}"
    if public_ip:
        head += f" · public IP {public_ip}"
    return {"ok": True, "status": 200, "detail": head + "\n" + "\n".join(lines),
            "records_total": safe_int(data.get("records_total"))}


# noinspection DuplicatedCode
# The live-fetch-then-format opening (print + try/fetch_data force=True +
# ValueError/RuntimeError guard) is the deliberate per-app status-skill twin
# shared with every other module (radarr / sonarr / … — CLAUDE.md). The
# formatted output is app-specific, so it stays inline.
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch + format the records summary. Never raises."""
    print(f"[ddns] INFO ddns_status host={host_id} svc_idx={service_idx} (live fetch)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[ddns] warning: ddns_status host={host_id} could not fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    total = safe_int(data.get("records_total"))
    up = safe_int(data.get("up_count"))
    fail = safe_int(data.get("fail_count"))
    stale = safe_int(data.get("stale_count"))
    stale_hrs = safe_int(data.get("stale_after_hours"))
    ip = str(data.get("public_ip") or "").strip()
    failing = as_list(data.get("failing_domains"))
    lines = [
        f"{'✅' if fail == 0 and total else '⚠️'} Records: {up}/{total} up to date",
    ]
    if ip:
        lines.append(f"🌐 Public IP: {ip}")
    if fail:
        lines.append(f"❌ Failing: {fail}")
        if failing:
            lines.append("   " + ", ".join(str(d) for d in failing))
    if stale:
        lines.append(f"🕒 Stale (no update in >{stale_hrs}h): {stale}")
    return {"ok": True, "status": 200, "detail": "\n".join(lines),
            "records_total": total, "up_count": up, "fail_count": fail,
            "stale_count": stale, "public_ip": ip}


async def _update_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None) -> dict:
    """Action: trigger an update of every record (GET /update). Never raises.

    ddns-updater's ``GET /update`` triggers the update then 302-redirects back
    to the web-UI root. Some builds close the keep-alive connection right after
    that 302, so a pooled connection reused for the FOLLOWED redirect surfaces
    as ``RemoteProtocolError: Server disconnected without sending a response``.
    Forcing a fresh connection per request (``max_keepalive_connections=0`` —
    no socket is pooled, so the followed redirect opens a new one) plus one
    retry on a transient protocol error recovers it; the update itself is
    idempotent (re-pushes the current IP), so the retry is safe. A 3xx is
    itself success — the update WAS triggered before the redirect fired.
    """
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    url = base + "/update"
    print(f"[ddns] INFO ddns_update host={host_id}")
    limits = httpx.Limits(max_keepalive_connections=0)
    last_err: Optional[Exception] = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(verify=False, timeout=30.0,
                                         follow_redirects=True,
                                         limits=limits) as cli:
                r = await cli.get(url)
        except httpx.RemoteProtocolError as e:  # transient — retry once
            last_err = e
            print(f"[ddns] warning: update host={host_id} attempt {attempt + 1} "
                  f"transient disconnect — {type(e).__name__}: {e}")
            continue
        except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
            print(f"[ddns] warning: update host={host_id} failed — {type(e).__name__}: {e}")
            return {"ok": False, "status": 0, "detail": f"update failed: {type(e).__name__}: {e}"}
        if 200 <= r.status_code < 400:
            return {"ok": True, "status": r.status_code,
                    "detail": "🔄 Triggered a DNS update — ddns-updater is refreshing "
                              "every record now."}
        return {"ok": False, "status": r.status_code,
                "detail": f"update returned HTTP {r.status_code}"}
    return {"ok": False, "status": 0,
            "detail": f"update failed after retry: {type(last_err).__name__}: {last_err}"}
