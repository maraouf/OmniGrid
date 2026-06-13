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
from logic.external_urls import ExternalURL

# Public image-CDN host SUFFIXES that *arr ``remoteUrl`` posters point at
# (TMDB for Radarr, TheTVDB for Sonarr, fanart / coverart / TheAudioDB for the
# music + book apps). These are PUBLIC images, so the per-app image proxy
# fetches them anonymously (NO api_key) — not an SSRF / credential-leak concern.
# Remote-first is far more reliable than the local ``/MediaCover`` path, which
# authenticates differently across *arr versions / reverse-proxy setups (a
# header-or-query mismatch makes the app serve its 200 HTML SPA shell for the
# image route → the proxy 415s → an empty poster). Suffix match so any subdomain
# (``artworks.thetvdb.com`` / ``assets.fanart.tv`` / ``ia800.us.archive.org``)
# passes.
_REMOTE_POSTER_HOST_SUFFIXES = (
    ExternalURL.TMDB_IMAGE_HOST,  # image.tmdb.org — Radarr (TMDB)
    "thetvdb.com",  # Sonarr (TVDB artworks)
    "fanart.tv",  # Radarr / Sonarr fanart
    "coverartarchive.org",  # Lidarr (MusicBrainz cover art)
    "archive.org",  # coverartarchive redirects to ia*.archive.org
    "theaudiodb.com",  # Lidarr (TheAudioDB)
    "gr-assets.com",  # Readarr (Goodreads asset CDN)
    "goodreads.com",  # Readarr (Goodreads)
    "media-amazon.com",  # Readarr (Amazon book covers)
    "ssl-images-amazon.com",  # Readarr (Amazon book covers)
    "openlibrary.org",  # Readarr (Open Library covers)
    "books.google.com",  # Readarr (Google Books covers)
    "googleusercontent.com",  # Readarr (Google Books image CDN)
)


def _is_remote_poster_host(host: str) -> bool:
    """True when ``host`` is (a subdomain of) a known public image CDN — the
    only absolute URLs the per-app image proxy will fetch for a *arr poster."""
    h = (host or "").strip().lower()
    return bool(h) and any(h == s or h.endswith("." + s)
                           for s in _REMOTE_POSTER_HOST_SUFFIXES)


def image_debug(item: Any) -> str:
    """Compact ``coverType=host`` summary of a *arr item's ``images`` remote
    hosts — for a one-line diagnostic log when a poster won't resolve, so the
    exact CDN host to allowlist is visible without guessing. ``'no-images'``
    when the embed carries none."""
    if not isinstance(item, dict):
        return "not-a-dict"
    imgs = item.get("images")
    if not isinstance(imgs, list) or not imgs:
        return "no-images"
    from urllib.parse import urlsplit  # noqa: PLC0415
    parts = []
    for im in imgs[:4]:
        if not isinstance(im, dict):
            continue
        ct = str(im.get("coverType") or "?").strip().lower()
        host = (urlsplit(str(im.get("remoteUrl") or "")).hostname or "local").lower()
        parts.append(f"{ct}={host}")
    return ", ".join(parts) or "no-images"


# 1 GiB in bytes — the *arr apps report disk space in bytes; the cards render
# GiB (matching each app's own UI).
GIB = 1024 ** 3
# Cap on how many mounts a card surfaces — an *arr with a long remote-mount
# list shouldn't render an unbounded stack. Sorted by total descending first,
# so the largest (real library) volumes win.
DISK_DISPLAY_MAX = 8


def headers(key: str) -> dict:
    """Standard *arr auth headers — the API key in ``X-Api-Key`` + a JSON
    Accept. Shared by every *arr module so the header shape lives in one place."""
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


def storage_summary_line(disks: list, free_gb: Any = 0.0) -> str:
    """Compact ONE-line storage summary for a status skill's text ``detail`` (the
    AI / Telegram surface): total free / total across every reported mount + the
    mount count. The web drawer renders the per-mount CARDS from the result's
    ``disks`` field instead (see the skill-result storage block), so the verbose
    per-mount breakdown lives there, not as a wall of text. Falls back to the
    largest-disk free figure when no per-mount data; ``""`` when nothing known."""
    ds = [d for d in disks
          if isinstance(d, dict) and safe_float(d.get("total_gb")) > 0]
    if ds:
        free = sum(safe_float(d.get("free_gb")) for d in ds)
        total = sum(safe_float(d.get("total_gb")) for d in ds)
        n = len(ds)
        unit = "mount" if n == 1 else "mounts"
        return (f"💾 Storage: {fmt_size_gib(free)} free / {fmt_size_gib(total)} "
                f"across {n} {unit}")
    if safe_float(free_gb) > 0:
        return f"💾 Disk free: {fmt_size_gib(free_gb)}"
    return ""


def year_suffix(year: Any) -> str:
    """`` (2024)`` when ``year`` is a plausible film / show year, else ``""``."""
    y = safe_int(year)
    return f" ({y})" if 1870 < y < 2100 else ""


def poster_url(item: Any) -> str:
    """Best-effort poster URL for a *arr item (movie / series / artist / book /
    album).

    Every *arr item carries an ``images`` list of ``{coverType, url,
    remoteUrl}``. We prefer ``remoteUrl`` — the TMDB / TVDB / MusicBrainz CDN
    URL — because the SPA can fetch it through the in-app image proxy
    (``proxiedImageUrl`` → ``/api/image-proxy``) without needing the *arr
    api_key; the local ``url`` (``/MediaCover/...``) would require the key on
    the wire. Prefers ``poster`` art (movies / series / artists), then falls
    back to ``cover`` (Lidarr ALBUM art uses coverType ``cover``, not
    ``poster``). Returns ``""`` when no usable image is present (graceful — the
    UI then just shows the title with no thumbnail)."""
    if not isinstance(item, dict):
        return ""
    imgs = item.get("images")
    if not isinstance(imgs, list):
        return ""
    for want in ("poster", "cover"):
        for im in imgs:
            if isinstance(im, dict) and str(im.get("coverType") or "").lower() == want:
                url = str(im.get("remoteUrl") or "").strip()
                if url:
                    return url
    return ""


# noinspection DuplicatedCode
def remote_poster_url(item: Any) -> str:
    """The allowlisted PUBLIC-CDN ``remoteUrl`` for a *arr item (``poster`` then
    ``cover`` art), or ``""`` when none. This is the RELIABLE half of
    ``poster_proxy_path`` — fetched anonymously by the per-app proxy, no api_key,
    no ``/MediaCover`` auth quirk. Exposed separately so a music / book module
    can PREFER it over a derived public fallback (Cover Art Archive) and only
    drop to the 415-prone local path last."""
    if not isinstance(item, dict):
        return ""
    imgs = item.get("images")
    if not isinstance(imgs, list):
        return ""
    from urllib.parse import urlsplit  # noqa: PLC0415
    for want in ("poster", "cover"):
        for im in imgs:
            if isinstance(im, dict) and str(im.get("coverType") or "").lower() == want:
                rurl = str(im.get("remoteUrl") or "").strip()
                if rurl and "://" in rurl and _is_remote_poster_host(urlsplit(rurl).hostname or ""):
                    return rurl
    return ""


def local_poster_path_only(item: Any, *, id_fallback: bool = False) -> str:
    """The LOCAL ``/MediaCover/...`` poster path for a *arr item (``poster`` then
    ``cover``), or ``""``. The 415-prone half of ``poster_proxy_path`` — used
    only as a last resort after the remote + derived-public options."""
    if isinstance(item, dict):
        imgs = item.get("images")
        if isinstance(imgs, list):
            for want in ("poster", "cover"):
                for im in imgs:
                    if isinstance(im, dict) and str(im.get("coverType") or "").lower() == want:
                        url = str(im.get("url") or "").strip()
                        if url and "://" not in url:
                            return url if url.startswith("/") else "/" + url
        if id_fallback:
            mid = safe_int(item.get("id"))
            if mid:
                return f"/MediaCover/{mid}/poster.jpg"
    return ""


def poster_proxy_path(item: Any, *, id_fallback: bool = False) -> str:
    """Return a *arr item's poster reference to be routed through the per-app
    image proxy. REMOTE-FIRST:

    1. Prefer the absolute ``remoteUrl`` (TMDB / TheTVDB / fanart / coverart
       CDN) when its host is a known PUBLIC image CDN — the proxy fetches those
       anonymously, which is FAR more reliable than the local ``/MediaCover``
       path. ``/MediaCover`` authenticates differently across *arr versions /
       reverse-proxy setups (a header-or-query mismatch makes the app serve its
       200 HTML SPA shell for the image route → the proxy 415s → empty poster).
    2. Fall back to the local ``/MediaCover/...`` path (fetched server-side with
       the api_key) when no usable remote URL exists. Path normalised to a
       leading ``/``; any ``?lastWrite=`` cache-buster is preserved.
    3. ``id_fallback`` (Radarr movie / Sonarr series — both use the flat
       ``/MediaCover/<id>/poster.jpg`` scheme): build the local path straight
       from the item's own ``id`` when the ``images`` array is absent / trimmed.

    Prefers ``poster`` then ``cover`` art (Lidarr ALBUM art is ``cover``).
    ``""`` when nothing usable. Pairs with ``poster_proxy: True`` on the rich
    item so the SPA routes it through ``/api/services/.../image-proxy``."""
    return (remote_poster_url(item)
            or local_poster_path_only(item, id_fallback=id_fallback))


# Back-compat alias — older call sites referenced ``local_poster_path`` before
# the remote-first switch. Keep the name resolvable so any straggler import
# doesn't break; new code uses ``poster_proxy_path``.
local_poster_path = poster_proxy_path


def image_proxy_url(host_row: dict, chip: dict, path: str) -> "tuple[str, dict]":
    """Per-app image-proxy hook shared by every *arr module. Resolves a poster
    reference (from ``poster_proxy_path``) to ``(absolute_url, headers)`` for a
    server-side fetch. TWO shapes:

    1. An ABSOLUTE ``remoteUrl`` whose host is an allowlisted PUBLIC image CDN
       (TMDB / TheTVDB / fanart / coverart / Amazon) → fetched anonymously, NO
       api_key. This is the reliable path (the CDN needs no auth) and the
       remote-first default from ``poster_proxy_path``.
    2. A clean LOCAL ``/MediaCover/...`` path → joined to the chip's OWN base
       and fetched with the ``X-Api-Key`` (header + ``apikey`` query for the
       versions that only honour one), so the key never reaches the browser.

    SSRF guard: an absolute URL is rejected unless its host is on the public-CDN
    allowlist; a relative path is rejected if it escapes (``..``) or isn't a
    leading-``/`` local path."""
    api_key = (chip.get("api_key") or "").strip()
    p = (path or "").strip()
    if not p:
        raise ValueError("empty image path")
    # 1. Absolute remoteUrl — public CDN allowlist, fetched anonymously.
    if "://" in p:
        from urllib.parse import urlsplit  # noqa: PLC0415
        if not _is_remote_poster_host(urlsplit(p).hostname or ""):
            raise ValueError("remote image host not allowed")
        return p, {"Accept": "*/*"}
    # 2. Local MediaCover path — joined to the chip base, fetched with the key.
    if not p.startswith("/") or ".." in p:
        raise ValueError("image must be a clean local path")
    base = resolve_base_url(host_row, chip)
    if not base:
        raise ValueError("no upstream URL configured")
    from urllib.parse import quote  # noqa: PLC0415
    # *arr MediaCover / static image routes authenticate via the `apikey` QUERY
    # param, NOT the X-Api-Key HEADER — the header authenticates the /api/<v>/
    # JSON routes only. Header-only, Radarr/Sonarr serve their 200 text/html SPA
    # shell for the image path, and the proxy route's image/* content check then
    # 415s → the poster renders empty. So append apikey to the path's query
    # (preserving any existing ?lastWrite= cache-buster). Keep the header too —
    # harmless, and helps any version that DOES honour it. Accept: */* (never
    # the headers() JSON Accept, which would 406 the binary fetch).
    sep = "&" if "?" in p else "?"
    url = base.rstrip("/") + p + sep + "apikey=" + quote(api_key, safe="")
    return url, {"X-Api-Key": api_key, "Accept": "*/*"}


def fmt_release_date(when: Any, actor_username: Optional[str] = None) -> str:
    """Reformat an upstream release / air date to the invoking user's date
    format (Settings -> Profile -> Formats).

    ``when`` is whatever the *arr calendar returned — ``"2025-10-03"`` or a full
    ISO timestamp (``"2025-10-03T00:00:00Z"``); we take the ``YYYY-MM-DD`` head.
    ``actor_username`` is the user whose ``run_skill`` dispatch triggered this
    (web: the authed user; Telegram: the linked user) — blank resolves to the
    canonical default format. Date-only (time tokens stripped). Falls back to the
    raw input on any parse / lookup failure, so a malformed or non-Gregorian
    value passes through unchanged rather than rendering wrong. Best-effort
    cosmetic — never raises."""
    s = str(when or "").strip()
    if not s:
        return ""
    from datetime import datetime
    try:
        d = datetime.strptime(s[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return s  # not a Gregorian YYYY-MM-DD head — leave untouched
    try:
        from logic.datetime_fmt import (
            apply_datetime_format, get_user_datetime_format, strip_time_tokens)
        fmt = strip_time_tokens(get_user_datetime_format(actor_username or ""))
        return apply_datetime_format(d, fmt)
    except (ValueError, TypeError, ImportError):
        return s


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
                          app_label: str, api_version: str) -> dict:
    """Probe an *arr's auth-required ``/api/<v>/system/status`` with the supplied
    X-Api-Key. Returns ``{ok, detail, status}`` for direct SPA consumption.
    Falls back to the chip's stored ``api_key`` when ``candidate_key`` is blank
    so a re-test after first save doesn't need a retype. ``api_version`` is
    REQUIRED (no default) — every *arr caller already passes its own (v3 for
    Radarr / Sonarr, v1 for Lidarr / Readarr / Prowlarr); a default would both
    flag the v3 callers as 'argument equals default' AND silently mis-probe a
    v1 app that forgot to pass it."""
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
    try:
        _body = (r.text or "")[:160]
    except (ValueError, TypeError):
        _body = ""
    return {"ok": False, "status": r.status_code,
            "detail": f"{app_label} returned HTTP {r.status_code} for {command}"
                      + (f" — {_body}" if _body else "")}


def _version_key(ver: Any) -> tuple:
    """Sortable key for a dotted-numeric *arr version (``"5.14.0.9383"``).
    Non-numeric segments coerce to 0 so a malformed value still sorts low
    rather than raising. Used only as the fallback when the ``/update`` feed
    doesn't flag a ``latest`` entry."""
    parts = re.split(r"[.\-+]", str(ver or "").strip())
    return tuple(safe_int(p) for p in parts)


# noinspection DuplicatedCode
async def check_update_skill(host_row: dict, chip: dict, *, app_label: str,
                             api_version: str, host_id: Optional[str] = None,
                             actor_username: Optional[str] = None) -> dict:
    """Read-only: compare the running version against the latest available on
    the app's configured update branch.

    For MANUALLY-managed (non-Docker) installs where the operator applies
    updates by hand. Reads the running version from
    ``GET /api/<v>/system/status`` and the available updates from
    ``GET /api/<v>/update`` (the *arr update feed — populated from the release
    branch regardless of the configured update MECHANISM, so it reports the
    latest version even when the mechanism is External / Script). Reports
    up-to-date vs update-available + the latest version's release date + a
    new/fixed change-count summary. Never raises — every failure comes back as
    ``{ok: False, detail}``."""
    tag = app_label.lower()
    api_key, base, err = resolve_skill_target(host_row, chip, app_label)
    if err:
        return err
    print(f"[{tag}] INFO check_update host={host_id}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            current = await fetch_version(cli, base, api_key, api_version)
            r = await cli.get(base + f"/api/{api_version}/update",
                              headers=headers(api_key))
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0,
                "detail": f"update check failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code,
                "detail": f"auth failed (check {app_label} api_key)"}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code,
                "detail": f"{app_label} returned HTTP {r.status_code} for /update"}
    try:
        rows = r.json()
    except (ValueError, TypeError):
        return {"ok": False, "status": 502, "detail": "non-JSON from upstream"}
    if not isinstance(rows, list):
        rows = []
    cur_disp = current or "unknown"
    # Prefer the explicit `latest` flag; fall back to the highest version row.
    latest: Optional[dict] = next(
        (u for u in rows if isinstance(u, dict) and u.get("latest")), None)
    if latest is None:
        cand = [u for u in rows
                if isinstance(u, dict) and str(u.get("version") or "").strip()]
        latest = max(cand, key=lambda u: _version_key(u.get("version"))) if cand else None
    if not latest:
        return {"ok": True, "status": 200,
                "detail": f"✅ {app_label} {cur_disp} — no update information "
                          f"reported on the current branch."}
    latest_ver = str(latest.get("version") or "").strip()
    up_to_date = bool(latest.get("installed")) or (
        bool(latest_ver) and latest_ver == (current or ""))
    if up_to_date or not latest_ver:
        return {"ok": True, "status": 200,
                "detail": f"✅ {app_label} is up to date ({cur_disp})."}
    detail = (f"⬆️ Update available for {app_label}: running {cur_disp} → "
              f"latest {latest_ver}")
    when = fmt_release_date(str(latest.get("releaseDate") or "")[:10], actor_username)
    if when:
        detail += f" (released {when})"
    changes = latest.get("changes")
    if isinstance(changes, dict):
        new_n = len(changes.get("new") or []) if isinstance(changes.get("new"), list) else 0
        fix_n = len(changes.get("fixed") or []) if isinstance(changes.get("fixed"), list) else 0
        if new_n or fix_n:
            detail += f" — {new_n} new, {fix_n} fixed"
    detail += ". Use the Update action to install it via the built-in updater."
    return {"ok": True, "status": 200, "detail": detail}


async def app_update_skill(host_row: dict, chip: dict, *, app_label: str,
                           api_version: str, host_id: Optional[str] = None) -> dict:
    """Destructive: trigger the app's BUILT-IN updater via
    ``POST /api/<v>/command {name: "ApplicationUpdate"}``. The app downloads,
    installs and restarts itself.

    Only meaningful when the app's Update mechanism is "Built-in" (the default
    for native / non-Docker installs); a Docker install's "Docker" mechanism
    refuses it — which is exactly why this skill is hidden / refused for
    Docker-linked chips (their updates flow through the inline Docker Update
    action instead). Delegates to the shared ``command_skill`` so the auth /
    error handling lives in one place. Never raises."""
    return await command_skill(
        host_row, chip, command="ApplicationUpdate",
        started_msg=(f"⬆️ Started the built-in updater on {app_label}. It will "
                     f"download, install and restart automatically — give it a "
                     f"minute, then re-check the version."),
        app_label=app_label, api_version=api_version, host_id=host_id)


# --- Release-calendar item enrichment (Homarr-style detail rows) ----------
def web_base(chip: dict) -> str:
    """The operator-configured web URL for an *arr chip — the deep-link base
    for an item's "open in <App>" pill. '' when the chip has no ``url``."""
    return str((chip or {}).get("url") or "").strip().rstrip("/")


def imdb_url(imdb_id) -> str:
    """IMDb title-page URL for an ``imdbId`` (``tt…``); '' when absent/invalid."""
    iid = str(imdb_id or "").strip()
    return f"{ExternalURL.IMDB_WEB}/title/{iid}/" if iid.startswith("tt") else ""


def tmdb_movie_url(tmdb_id) -> str:
    """TMDB movie-page URL for a numeric ``tmdbId``; '' when absent/zero."""
    tid = str(tmdb_id or "").strip()
    return f"{ExternalURL.THEMOVIEDB_WEB}/movie/{tid}" if (tid.isdigit() and tid != "0") else ""


def release_time(iso) -> str:
    """``"HH:MM"`` from an ISO-8601 datetime when it carries a non-midnight
    time (e.g. Sonarr ``airDateUtc``); '' for a date-only / midnight value."""
    s = str(iso or "")
    if "T" not in s or len(s) < 16:
        return ""
    hhmm = s[11:16]
    return "" if hhmm in ("00:00", "0:00") else hhmm


def clamp_overview(text, limit: int = 240) -> str:
    """Collapse whitespace + cap an overview/synopsis to ``limit`` chars
    (ellipsised) so a long synopsis can't blow out the popover row."""
    s = " ".join(str(text or "").split())
    return (s[:limit].rstrip() + "…") if len(s) > limit else s


async def fetch_calendar(host_row: dict, chip: dict, *, api_version: str,
                         start: str, end: str, app_label: str,
                         extra_params: Optional[dict] = None) -> list:
    """Shared *arr calendar fetch for the release-calendar widget. GETs
    ``/api/<v>/calendar?start=&end=&unmonitored=false`` over the ISO-8601
    ``start`` / ``end`` window (``"YYYY-MM-DDTHH:MM:SSZ"``) and returns the raw
    upstream item list. ``extra_params`` merges per-app flags (Sonarr
    ``includeSeries`` / Lidarr ``includeArtist`` / Readarr ``includeAuthor``).

    Returns ``[]`` on ANY failure (unset key / unreachable / non-200 / non-JSON)
    — the widget tolerates a per-instance miss and just shows the other
    services. Never raises."""
    tag = app_label.lower()
    api_key, base, err = resolve_skill_target(host_row, chip, app_label)
    if err:
        return []
    params = {"start": start, "end": end, "unmonitored": "false"}
    if extra_params:
        params.update(extra_params)
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0,
                                     follow_redirects=True) as cli:
            r = await cli.get(base + f"/api/{api_version}/calendar",
                              headers=headers(api_key), params=params)
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        print(f"[{tag}] warning: calendar fetch failed — {type(e).__name__}: {e}")
        return []
    if r.status_code != 200:
        print(f"[{tag}] warning: calendar returned HTTP {r.status_code}")
        return []
    try:
        items = r.json()
    except (ValueError, TypeError):
        return []
    return items if isinstance(items, list) else []


async def queue_delete_skill(host_row: dict, chip: dict, *, arg: Optional[str],
                             app_label: str, api_version: str,
                             host_id: Optional[str] = None) -> dict:
    """Destructive action shared by every *arr: remove ONE record from the
    download queue (``DELETE /api/<v>/queue/<id>?removeFromClient=true&
    blocklist=false``). The drawer's per-row trash button supplies the numeric
    queue-record id. Never raises."""
    tag = app_label.lower()
    qid = (arg or "").strip()
    if not qid.isdigit():
        return {"ok": False, "status": 0,
                "detail": "no valid queue id given (the trash button supplies it)"}
    api_key, base, err = resolve_skill_target(host_row, chip, app_label)
    if err:
        return err
    print(f"[{tag}] INFO queue_delete host={host_id} id={qid}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0,
                                     follow_redirects=True) as cli:
            r = await cli.delete(base + f"/api/{api_version}/queue/{qid}",
                                 headers=headers(api_key),
                                 params={"removeFromClient": "true", "blocklist": "false"})
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"delete failed: {type(e).__name__}: {e}"}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code,
                "detail": f"auth failed (check {app_label} api_key)"}
    if 200 <= r.status_code < 300:
        return {"ok": True, "status": 200, "detail": "🗑️ Removed from the download queue."}
    return {"ok": False, "status": r.status_code, "detail": f"HTTP {r.status_code}"}


async def fetch_today_calendar_count(host_row: dict, chip: dict, *,
                                     api_version: str, app_label: str,
                                     extra_params: Optional[dict] = None) -> int:
    """How many calendar entries (movies releasing / episodes airing / albums /
    books) land TODAY in the instance's UTC-day window. Delegates to
    ``fetch_calendar`` over a ``[today 00:00Z, tomorrow 00:00Z)`` window and
    returns the entry count — surfaced as the card's "Today" chip. Returns 0 on
    ANY failure (the shared fetch tolerates an unset key / unreachable / non-200
    / non-JSON). Never raises."""
    from datetime import datetime, timedelta, timezone  # noqa: PLC0415
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    items = await fetch_calendar(
        host_row, chip, api_version=api_version,
        start=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        end=(start + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        app_label=app_label, extra_params=extra_params)
    return len(items)


async def queue_blocklist_search_skill(host_row: dict, chip: dict, *,
                                       arg: Optional[str], app_label: str,
                                       api_version: str, parent_id_field: str,
                                       search_command: str, search_ids_field: str,
                                       host_id: Optional[str] = None) -> dict:
    """Destructive "blocklist & search" for a STUCK queue item — the action
    operators reach for on a stalled grab. Removes the record from the queue
    AND blocklists the release (``DELETE /api/<v>/queue/<id>?removeFromClient=
    true&blocklist=true``) so the same bad release isn't re-grabbed, then kicks
    a fresh search for the parent (``POST /api/<v>/command {name:<search_command>
    , <search_ids_field>:[parent_id]}``).

    ``arg`` is ``"<queue_id>:<parent_id>"`` from the drawer's per-row button
    (the queue rich-list has both ids in scope). A bare ``"<queue_id>"`` (e.g.
    from an AI/Telegram dispatch) triggers a queue lookup to resolve the parent
    via ``parent_id_field``. Never raises."""
    tag = app_label.lower()
    raw = (arg or "").strip()
    qid, _, pid = raw.partition(":")
    qid = qid.strip()
    pid = pid.strip()
    if not qid.isdigit():
        return {"ok": False, "status": 0,
                "detail": "no valid queue id given (the blocklist button supplies it)"}
    api_key, base, err = resolve_skill_target(host_row, chip, app_label)
    if err:
        return err
    print(f"[{tag}] INFO queue_blocklist_search host={host_id} qid={qid} pid={pid or '?'}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=25.0,
                                     follow_redirects=True) as cli:
            # Resolve the parent id from the live queue when the button didn't
            # carry it (AI/Telegram path).
            if not pid.isdigit():
                try:
                    qr = await cli.get(base + f"/api/{api_version}/queue",
                                       headers=headers(api_key),
                                       params={"pageSize": "200"})
                    if qr.status_code == 200:
                        body = qr.json()
                        recs = body.get("records") if isinstance(body, dict) else None
                        for rec in (recs if isinstance(recs, list) else []):
                            if isinstance(rec, dict) and str(rec.get("id") or "") == qid:
                                pid = str(safe_int(rec.get(parent_id_field)) or "")
                                break
                except (httpx.HTTPError, OSError, ValueError, TypeError):
                    pid = ""
            # Remove + blocklist the stuck release.
            dr = await cli.delete(base + f"/api/{api_version}/queue/{qid}",
                                  headers=headers(api_key),
                                  params={"removeFromClient": "true", "blocklist": "true"})
            if dr.status_code in (401, 403):
                return {"ok": False, "status": dr.status_code,
                        "detail": f"auth failed (check {app_label} api_key)"}
            if not (200 <= dr.status_code < 300):
                return {"ok": False, "status": dr.status_code,
                        "detail": f"HTTP {dr.status_code} removing the queue item"}
            # Kick a fresh search for the parent so the indexer re-grabs.
            searched = False
            if pid.isdigit():
                try:
                    pr = await cli.post(base + f"/api/{api_version}/command",
                                        headers=headers(api_key),
                                        json={"name": search_command,
                                              search_ids_field: [int(pid)]})
                    searched = pr.status_code in (200, 201)
                except (httpx.HTTPError, OSError):
                    searched = False
    except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
        return {"ok": False, "status": 0,
                "detail": f"blocklist & search failed: {type(e).__name__}: {e}"}
    if searched:
        return {"ok": True, "status": 200,
                "detail": "🚫🔍 Blocklisted the release and started a fresh search."}
    return {"ok": True, "status": 200,
            "detail": "🚫 Blocklisted & removed the stuck release "
                      "(couldn't auto-search — trigger a search manually)."}
