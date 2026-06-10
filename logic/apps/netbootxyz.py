"""netboot.xyz per-app module (netbootxyz/webapp).

Encapsulates everything netboot.xyz-specific so the route layer
(``main_pkg/apps_routes.py``) stays generic.

What this is
------------
netboot.xyz is a network-boot menu manager — a web UI that downloads + serves
iPXE boot menus / kernels over TFTP + HTTP so a machine can PXE-boot installers
and live OSes. The management webapp (``ghcr.io/netbootxyz/netbootxyz``, default
port 3000) has NO authentication and its dynamic data (local / remote boot
assets, menus) flows over **socket.io**, not a rich REST API. The reliable HTTP
surface is just:

    GET  /          — the web UI (reachability)
    GET  /version   — the running version (best-effort: JSON or a plain string)

So this module is a defensive STATUS + VERSION card, mirroring the no-auth
``ddns_updater`` shape: ``requires_api_key()`` is False — the editor only needs
the instance URL (the generic chip URL field, pointing at the webapp root) + a
cache TTL. The expanded card answers "is netboot.xyz up, and what version is it
running (is an update available)" at a glance.

    available         — reached + parsed
    version           — the running version (when /version exposes it)
    latest            — the latest version (when /version reports a remote)
    update_available  — version != latest (only when both are known)

AI / Telegram skills
--------------------
* ``netbootxyz_status`` — reachability + version (+ update-available).

Single-instance app (NOT fleet) — one card per pinned chip.
"""
from __future__ import annotations

import re
import time
from typing import Optional

import httpx

from logic.apps._common import (
    cache_key, fetch_gate, peek_cache, resolve_base_url, resolve_cache_ttl)
from logic.coerce import as_dict, safe_int

# Catalog template slugs handled by this module (the built-in template is
# `netboot-xyz`; `netbootxyz` covers an operator-renamed chip).
SLUGS: tuple[str, ...] = ("netboot-xyz", "netbootxyz")

DEFAULT_CACHE_TTL_S = 120
_data_cache: dict[str, tuple[float, dict]] = {}

# A version-ish token (v2.0.84 / 2.0.84 / 0.7) for the text / fallback path.
_VERSION_RE = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b")

SKILLS: tuple[dict, ...] = (
    {
        "id": "netbootxyz_status",
        "name": "netboot.xyz status",
        "ai_phrases": ("netboot status, netboot.xyz status, is netboot up, "
                       "pxe boot status, network boot server, what version is "
                       "netboot.xyz, is netboot.xyz reachable, netbootxyz health"),
        "destructive": False,
    },
)


def requires_api_key() -> bool:
    """False — the netboot.xyz webapp has NO authentication; the editor only
    needs the instance URL (its web UI root) + a cache TTL."""
    return False


def _parse_version(resp: "httpx.Response") -> "tuple[str, str]":
    """Best-effort ``(version, latest)`` from a ``/version`` response. Handles
    a JSON object (several key spellings), a JSON string, and a plain-text
    version. Returns ``("", "")`` when nothing version-like is found."""
    ctype = (resp.headers.get("content-type") or "").lower()
    if "json" in ctype:
        try:
            body = resp.json()
        except (ValueError, TypeError):
            body = None
        if isinstance(body, dict):
            d = as_dict(body)
            ver = str(d.get("version") or d.get("local") or d.get("localVersion")
                      or d.get("current") or "").strip()
            lat = str(d.get("remote") or d.get("remoteVersion") or d.get("latest")
                      or "").strip()
            return ver, lat
        if isinstance(body, str):
            return body.strip(), ""
    txt = (resp.text or "").strip()
    # A short, non-HTML body is a plain version string ("2.0.84").
    if txt and "<" not in txt and len(txt) <= 40:
        return txt, ""
    m = _VERSION_RE.search(txt)
    return (m.group() if m else ""), ""


async def _probe_version(cli: "httpx.AsyncClient", base: str) -> "tuple[str, str]":
    """GET ``/version`` and parse it (best-effort). Returns ``("", "")`` on any
    failure — a missing /version endpoint is non-fatal (the card still shows
    'reachable')."""
    try:
        r = await cli.get(base + "/version")
    except (httpx.HTTPError, OSError):
        return "", ""
    if not (200 <= r.status_code < 300):
        return "", ""
    return _parse_version(r)


# noinspection PyUnusedLocal
async def test_credential(host_row: dict, chip: dict, candidate_key: str, *,
                          payload: Optional[dict] = None, **_kw) -> dict:
    """Probe the netboot.xyz web UI (``GET /``). No auth — ``candidate_key`` /
    ``payload`` are part of the generic route contract but unused. Returns
    ``{ok, detail, status}``."""
    base = resolve_base_url(host_row, chip)
    if not base:
        return {"ok": False, "detail": "no upstream URL configured", "status": 0}
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + "/")
            if not (200 <= r.status_code < 400):
                return {"ok": False, "detail": f"HTTP {r.status_code}",
                        "status": r.status_code}
            ver, _lat = await _probe_version(cli, base)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    detail = f"OK (netboot.xyz {ver})" if ver else "OK (reachable)"
    return {"ok": True, "detail": detail, "status": 200}


async def fetch_data(host_row: dict, chip: dict, *,
                     host_id: str, service_idx: int,
                     force: bool = False) -> dict:
    """Probe the netboot.xyz webapp for the expanded card. Returns
    ``{available, version, latest, update_available, fetched_at}``. Raises
    ``ValueError`` (base URL won't resolve) / ``RuntimeError`` (upstream error)."""
    now = time.time()
    # No-auth app — pass credential=True so the gate never raises on a missing
    # secret (it folds the URL-resolve + cache-miss-log shape shared with the
    # other fetch_data openers, so this isn't a structural twin of them).
    base, hit = fetch_gate(host_row, chip, host_id, service_idx, _data_cache,
                           resolve_cache_ttl(chip, DEFAULT_CACHE_TTL_S), now, force,
                           credential=True, log_tag="netbootxyz")
    if hit is not None:
        return hit
    url = base + "/"
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url)
            if not (200 <= r.status_code < 400):
                print(f"[netbootxyz] error: fetch host={host_id} url={url} returned "
                      f"HTTP {r.status_code} (check the chip URL points at the "
                      f"netboot.xyz webapp root, e.g. http://host:3000)")
                raise RuntimeError(f"upstream returned HTTP {r.status_code} for {url}")
            version, latest = await _probe_version(cli, base)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[netbootxyz] error: fetch host={host_id} url={url} "
              f"failed — {type(e).__name__}: {e}")
        raise RuntimeError(f"upstream fetch failed: {type(e).__name__}: {e}")
    update_available = bool(version and latest and version != latest)
    out: dict = {
        "available": True,
        "version": version,
        "latest": latest,
        "update_available": update_available,
        "fetched_at": int(now),
    }
    print(f"[netbootxyz] INFO fetched host={host_id} version={version or '-'} "
          f"latest={latest or '-'} update={update_available}")
    _data_cache[cache_key(host_id, service_idx)] = (now, out)
    return out


def peek_latest(host_id: str, service_idx: int) -> Optional[dict]:
    """Cache-only peek (no upstream call) for the AI context's
    ``app_skills[].last``."""
    data = peek_cache(_data_cache, host_id, service_idx)
    if not isinstance(data, dict) or not data.get("available"):
        return None
    return {
        "version": data.get("version") or "",
        "latest": data.get("latest") or "",
        "update_available": bool(data.get("update_available")),
        "fetched_at": safe_int(data.get("fetched_at")),
    }


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
async def run_skill(skill_id: str, host_row: dict, chip: dict, *,
                    host_id: Optional[str] = None,
                    service_idx: Optional[int] = None, **_kw) -> dict:
    """Dispatch one of this app's SKILLS. Raises ValueError on an unknown id."""
    if skill_id == "netbootxyz_status":
        return await _status_skill(host_row, chip, host_id=host_id,
                                   service_idx=service_idx)
    raise ValueError(f"unknown skill: {skill_id!r}")


# noinspection DuplicatedCode
# The live-fetch-then-format opening (print + try/fetch_data force=True +
# ValueError/RuntimeError guard) is the deliberate per-app status-skill twin
# shared with every other module (radarr / ddns / … — CLAUDE.md). The formatted
# output is app-specific, so it stays inline.
async def _status_skill(host_row: dict, chip: dict, *,
                        host_id: Optional[str] = None,
                        service_idx: Optional[int] = None) -> dict:
    """Read-only: live-fetch + format the reachability + version summary. Never
    raises."""
    print(f"[netbootxyz] INFO netbootxyz_status host={host_id} svc_idx={service_idx} "
          f"(live fetch)")
    try:
        data = await fetch_data(host_row, chip, host_id=str(host_id or ""),
                                service_idx=int(service_idx or 0), force=True)
    except (ValueError, RuntimeError) as e:
        print(f"[netbootxyz] warning: netbootxyz_status host={host_id} could not "
              f"fetch — {e}")
        return {"ok": False, "detail": str(e), "status": 0}
    version = str(data.get("version") or "").strip()
    latest = str(data.get("latest") or "").strip()
    if version:
        lines = [f"🥾 netboot.xyz is up — running {version}"]
    else:
        lines = ["🥾 netboot.xyz is up and reachable."]
    if data.get("update_available") and latest:
        lines.append(f"⬆️ Update available: {latest}")
    return {"ok": True, "status": 200, "detail": "\n".join(lines),
            "version": version, "latest": latest,
            "update_available": bool(data.get("update_available"))}
