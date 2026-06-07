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
from logic.coerce import safe_int

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


def requires_api_key() -> bool:
    """False — ddns-updater's web UI / update endpoint have NO auth; the
    editor only needs the instance URL + a cache TTL."""
    return False


def _clean_cell(raw: str) -> str:
    """Strip inner HTML tags + unescape entities from a table cell."""
    return _html.unescape(_TAG_RE.sub("", raw or "")).strip()


def _classify(status: Any) -> str:
    """Map a ddns-updater record status to ok / fail / pending."""
    s = str(status or "").strip().lower()
    if s in ("success", "up to date"):
        return "ok"
    if s in ("failure", "fail", "error"):
        return "fail"
    return "pending"  # updating / unset / unknown


def _parse_records(html_text: Any) -> list[dict]:
    """Parse the ddns-updater web-UI HTML into a list of record dicts
    ``{domain, owner, provider, ip_version, status, current_ip}``. Defensive
    over malformed HTML (returns ``[]``). Keys on the per-cell ``data-label``
    attribute, so column reordering doesn't break it."""
    if not isinstance(html_text, str) or not html_text:
        return []
    out: list[dict] = []
    for row in _ROW_RE.finditer(html_text):
        cells = {k.strip().lower(): _clean_cell(v)
                 for k, v in _CELL_RE.findall(row.group(1))}
        if not cells or ("domain" not in cells and "update status" not in cells):
            continue
        out.append({
            "domain": cells.get("domain", ""),
            "owner": cells.get("owner", ""),
            "provider": cells.get("provider", ""),
            "ip_version": cells.get("ip version", ""),
            "status": cells.get("update status", ""),
            "current_ip": cells.get("current ip", ""),
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
    public_ip = ""
    for r in records:
        m = _IP_RE.search(str(r.get("current_ip") or ""))
        if m:
            public_ip = m.group()
            break
    return {
        "records_total": total,
        "up_count": up,
        "fail_count": fail,
        "public_ip": public_ip,
        "failing_domains": failing[:8],
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
    print(f"[ddns] INFO fetched host={host_id} records={out['records_total']} "
          f"up={out['up_count']} fail={out['fail_count']} ip={out['public_ip']}")
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
    if skill_id == "ddns_update":
        return await _update_skill(host_row, chip, host_id=host_id)
    raise ValueError(f"unknown skill: {skill_id!r}")


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
    ip = str(data.get("public_ip") or "").strip()
    failing = data.get("failing_domains") if isinstance(data.get("failing_domains"), list) else []
    lines = [
        f"{'✅' if fail == 0 and total else '⚠️'} Records: {up}/{total} up to date",
    ]
    if ip:
        lines.append(f"🌐 Public IP: {ip}")
    if fail:
        lines.append(f"❌ Failing: {fail}")
        if failing:
            lines.append("   " + ", ".join(str(d) for d in failing))
    return {"ok": True, "status": 200, "detail": "\n".join(lines),
            "records_total": total, "up_count": up, "fail_count": fail,
            "public_ip": ip}


async def _update_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None) -> dict:
    """Action: trigger an update of every record (GET /update). Never raises."""
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    print(f"[ddns] INFO ddns_update host={host_id}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=30.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/update")
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[ddns] warning: update host={host_id} failed — {type(e).__name__}: {e}")
        return {"ok": False, "status": 0, "detail": f"update failed: {type(e).__name__}: {e}"}
    if r.status_code in (200, 201, 202, 204):
        return {"ok": True, "status": r.status_code,
                "detail": "🔄 Triggered a DNS update — ddns-updater is refreshing "
                          "every record now."}
    return {"ok": False, "status": r.status_code,
            "detail": f"update returned HTTP {r.status_code}"}
