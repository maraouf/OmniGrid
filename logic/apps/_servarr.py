"""Shared Servarr-family (*arr) helpers — Radarr / Sonarr / Lidarr / Readarr.

These four apps all speak the same Servarr HTTP shape (an ``X-Api-Key`` header,
``/api/<v>/`` endpoints, and identical ``diskspace`` / ``system/status`` /
``command`` schemas), so the byte-identical helper bodies live here ONCE instead
of being copied into each of the four ~860-line modules. A module binds the
per-app variation with ``functools.partial`` so its existing call sites stay
unchanged; the three axes that vary are:

  - ``api_version`` — ``"v3"`` (Radarr / Sonarr) or ``"v1"`` (Lidarr / Readarr).
  - ``app_label``   — the brand name shown in ``detail`` strings + log tags.
  - ``id_field``    — the upstream unique id used by ``find_in_library_titled``
                      (``tmdbId`` for Radarr, ``tvdbId`` for Sonarr).

What STAYS per-module (genuinely app-specific, NOT shared): the library-counting
loop + output dict in ``fetch_data`` (movies/series/artists/authors differ), the
``add`` skill (Lidarr / Readarr require ``metadataProfileId``; Sonarr has a
``languageProfile`` v3/v4 quirk), the upstream lookup (TMDB vs MusicBrainz vs
Goodreads), and Lidarr / Readarr's own ``find_in_library`` (they match a STRING
``foreignArtistId`` / ``foreignAuthorId`` + an ``artistName`` / ``authorName``
field, not a numeric id + ``title``).

Dependency-free leaf relative to the per-app modules (imports only ``_common`` +
``coerce`` + ``httpx``) so importing it can't create a cycle.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import httpx

from logic.apps._common import resolve_base_url, resolve_credential_target
from logic.coerce import safe_float, safe_int

# 1 GiB in bytes — the *arr apps report disk space in bytes; the cards render
# GiB (matching each app's own UI).
GIB = 1024 ** 3
# Cap on how many mounts a card surfaces — an *arr with a long remote-mount
# list shouldn't render an unbounded stack. Sorted by total descending first,
# so the largest (real library) volumes win.
DISK_DISPLAY_MAX = 8


def headers(key: str) -> dict:
    return {"X-Api-Key": key, "Accept": "application/json"}


def version_from(resp) -> str:
    """Extract ``version`` from a ``/api/<v>/system/status`` response. Returns
    ``""`` on any non-200 / parse failure (version is a nice-to-have, never
    load-bearing)."""
    try:
        if getattr(resp, "status_code", 0) != 200:
            return ""
        body = resp.json() or {}
        return str(body.get("version") or "").strip()
    except (ValueError, TypeError, AttributeError):
        return ""


async def fetch_version(cli: httpx.AsyncClient, base: str, key: str,
                        api_version: str = "v3") -> str:
    """Best-effort *arr version via ``GET /api/<v>/system/status`` on an
    already-open client — shared by the credential probe + the card fetch.
    ``''`` on any failure (version is never load-bearing)."""
    try:
        return version_from(await cli.get(
            base + f"/api/{api_version}/system/status", headers=headers(key)))
    except (httpx.HTTPError, OSError):
        return ""


def fmt_size_gib(gib: Any) -> str:
    """Render a GiB value as a human size, promoting to TiB at >= 1024 GiB
    (matches the *arr GiB / TiB display). One decimal place. ``Any`` arg —
    callers pass raw ``dict.get(...)`` values; ``safe_float`` coerces (never
    raises), so a non-number falls back to 0."""
    g = safe_float(gib)
    if g >= 1024:
        return f"{g / 1024:,.1f} TiB"
    return f"{g:,.1f} GiB"


def parse_disks(raw: Any) -> list[dict]:
    """Shape a ``/api/<v>/diskspace`` payload (list of ``{path, label,
    freeSpace, totalSpace}``) into ``[{path, free_gb, total_gb}]`` for EVERY
    mount, sorted by total descending, capped at ``DISK_DISPLAY_MAX``. Skips
    no-path / zero-total entries. Free / total are GiB floats. ``[]`` on an
    empty / malformed payload."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        path = str(d.get("path") or "").strip()
        total = safe_float(d.get("totalSpace"))
        if not path or total <= 0:
            continue
        out.append({
            "path": path,
            "free_gb": round(safe_float(d.get("freeSpace")) / GIB, 1),
            "total_gb": round(total / GIB, 1),
        })
    out.sort(key=lambda m: m.get("total_gb", 0.0), reverse=True)
    return out[:DISK_DISPLAY_MAX]


def primary_disk(disks: list[dict]) -> "tuple[float, float]":
    """The largest mount's ``(free_gb, total_gb)`` — used by the status skill's
    one-line summary + the AI peek. ``(0.0, 0.0)`` when empty. ``disks`` is
    already sorted by total descending, so element 0 is it."""
    if not disks:
        return 0.0, 0.0
    d0 = disks[0]
    return safe_float(d0.get("free_gb")), safe_float(d0.get("total_gb"))


def year_suffix(year: Any) -> str:
    """`` (2024)`` when ``year`` is a plausible film / show year, else ``""``."""
    y = safe_int(year)
    return f" ({y})" if 1870 < y < 2100 else ""


def norm_title(s: Any) -> str:
    """Normalise a title / query for matching: lowercase, strip a trailing
    ``" (YYYY)"`` year suffix (the apps + our own replies append it, but the
    library ``title`` field has no year), collapse whitespace. So
    ``"Dora and the Lost City of Gold (2019)"`` matches the stored
    ``"Dora and the Lost City of Gold"``."""
    t = str(s or "").strip().lower()
    t = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", t)  # drop a trailing (YYYY)
    return re.sub(r"\s+", " ", t).strip()


def find_in_library_titled(items: Any, query: str, id_field: str) -> Optional[dict]:
    """Find an item in a library list by numeric ``id_field`` (``tmdbId`` /
    ``tvdbId``), then normalised-exact ``title``, then BIDIRECTIONAL title
    substring (so ``"Title (2019)"`` matches a stored ``"Title"`` and a partial
    query still hits). Radarr + Sonarr shape — Lidarr / Readarr match a STRING
    foreign id + a name field, so they keep their own finder. Returns the item
    dict or ``None``."""
    if not isinstance(items, list):
        return None
    raw = (query or "").strip()
    q = norm_title(raw)
    if not q:
        return None
    if raw.isdigit():
        tid = int(raw)
        for m in items:
            if isinstance(m, dict) and safe_int(m.get(id_field)) == tid:
                return m
    for m in items:
        if isinstance(m, dict) and norm_title(m.get("title")) == q:
            return m
    for m in items:
        if not isinstance(m, dict):
            continue
        t = norm_title(m.get("title"))
        if t and (q in t or t in q):
            return m
    return None


def resolve_skill_target(host_row: dict, chip: dict,
                         app_label: str) -> "tuple[str, str, Optional[dict]]":
    """Shared opening for the arg / read skills: resolve ``(api_key, base)`` or
    return a ready ``{ok: False, detail}``. ``app_label`` names the app in the
    "api_key not set" detail."""
    api_key = (chip.get("api_key") or "").strip()
    if not api_key:
        return "", "", {"ok": False, "status": 0, "detail": f"{app_label} api_key not set"}
    base = resolve_base_url(host_row, chip)
    if not base:
        return "", "", {"ok": False, "status": 0, "detail": "no upstream URL configured"}
    return api_key, base, None


async def test_credential(host_row: dict, chip: dict, candidate_key: str, *,
                          app_label: str, api_version: str = "v3") -> dict:
    """Probe an *arr's auth-required ``/api/<v>/system/status`` with the supplied
    X-Api-Key. Returns ``{ok, detail, status}`` for direct SPA consumption.
    Falls back to the chip's stored ``api_key`` when ``candidate_key`` is blank
    so a re-test after first save doesn't need a retype."""
    key, base, err = resolve_credential_target(host_row, chip, candidate_key)
    if err:
        return err
    url = base + f"/api/{api_version}/system/status"
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(url, headers=headers(key))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "status": 0}
    if r.status_code == 200:
        ver = version_from(r)
        return {"ok": True,
                "detail": f"OK ({app_label} {ver})" if ver else "OK",
                "status": 200}
    if r.status_code in (401, 403):
        return {"ok": False, "detail": "auth failed (check api_key)",
                "status": r.status_code}
    return {"ok": False, "detail": f"HTTP {r.status_code}", "status": r.status_code}


async def command_skill(host_row: dict, chip: dict, *, command: str,
                        started_msg: str, app_label: str,
                        api_version: str = "v3",
                        host_id: Optional[str] = None) -> dict:
    """Action skill: POST a non-destructive background command to an *arr's
    ``/api/<v>/command`` endpoint (e.g. ``MissingMoviesSearch`` /
    ``RefreshMovie``). Never raises — every failure comes back as
    ``{ok: False, detail}``."""
    tag = app_label.lower()
    api_key, base, err = resolve_skill_target(host_row, chip, app_label)
    if err:
        return err
    print(f"[{tag}] INFO command host={host_id} name={command!r}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.post(base + f"/api/{api_version}/command",
                               headers=headers(api_key), json={"name": command})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[{tag}] warning: command {command!r} failed — {type(e).__name__}: {e}")
        return {"ok": False, "status": 0,
                "detail": f"command failed: {type(e).__name__}: {e}"}
    if r.status_code in (200, 201):
        return {"ok": True, "status": r.status_code, "detail": started_msg}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code,
                "detail": f"auth failed (check {app_label} api_key)"}
    _body = ""
    try:
        _body = (r.text or "")[:160]
    except (ValueError, TypeError):
        _body = ""
    return {"ok": False, "status": r.status_code,
            "detail": f"{app_label} returned HTTP {r.status_code} for {command}"
                      + (f" — {_body}" if _body else "")}
