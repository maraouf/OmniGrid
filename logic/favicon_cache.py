"""Favicon proxy + on-disk cache for bookmark / app tiles.

The SPA's bookmark + app tiles resolve their icon brand-icon → catalog-icon →
**favicon-proxy** → letter-avatar. A direct client-side ``<host>/favicon.ico``
fetch is CORS / mixed-content fragile (and leaks the browser's egress), so the
final fallback routes through this server-side proxy: it fetches the site's
favicon (``/favicon.ico`` + the ``<link rel="icon">`` declared in the page
head), caches the bytes to disk, and serves them from OmniGrid's own origin.

Layout (mirrors ``logic/image_cache.py`` + the asset-inventory atomic-write
pattern): ``<data_dir>/favicons/<sha256(url)>`` holds the raw bytes (its mtime
is the cache time, for the TTL check) and ``<sha256>.ct`` holds the content
type. A ``<sha256>.miss`` zero-byte marker is a NEGATIVE cache so a site with no
resolvable favicon isn't re-fetched on every tile render (short 1-day TTL — a
site may add one later). All writes are atomic via ``.tmp`` + ``os.replace``.

SECURITY: the route layer owns the SSRF gate (``logic.url_safety.
host_resolves_public`` + the curated-host allow-list); ``fetch_favicon`` re-runs
the same gate on EVERY host it's about to hit (the page host AND a cross-host
``<link rel=icon href>``) and never follows redirects, so a public page can't
bounce the fetch onto an internal IP. Bytes are validated as an actual image
(content-type OR magic bytes) and size-capped.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Awaitable, Callable

from logic.env_keys import EnvKey, env_get
from logic.tuning import Tunable as _Tunable
from logic.tuning import tuning_int as _tuning_int

_DATA_DIR = (os.path.dirname(env_get(EnvKey.DB_PATH)) or ".") if env_get(EnvKey.DB_PATH) else "/tmp"
CACHE_DIR = os.path.join(_DATA_DIR, "favicons")

# Cap a fetched favicon body — real favicons are a few KB; 2 MB is a generous
# ceiling that still refuses a misbehaving / spoofing upstream.
_MAX_BYTES = 2 * 1024 * 1024
# Negative-cache TTL (a site with no favicon) — short so a later-added icon
# shows up within a day without an operator action.
_MISS_TTL_SECONDS = 86400


def ensure_dir() -> None:
    """Create the favicon cache directory (idempotent; best-effort)."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
    except OSError as e:
        print(f"[favicon] cache dir create failed: {e}")


def _ttl_seconds() -> int:
    return max(1, _tuning_int(_Tunable.FAVICON_CACHE_DAYS)) * 86400


def _timeout_seconds() -> float:
    return float(max(2, _tuning_int(_Tunable.FAVICON_FETCH_TIMEOUT_SECONDS)))


def _key(url: str) -> str:
    return hashlib.sha256((url or "").strip().encode(errors="replace")).hexdigest()


def _paths(url: str) -> "tuple[str, str, str]":
    base = os.path.join(CACHE_DIR, _key(url))
    return base, base + ".ct", base + ".miss"


def get(url: str) -> "tuple[bytes, str] | None":
    """Return ``(bytes, content_type)`` for a still-fresh cached favicon, else
    ``None``. A stale entry is removed so it re-fetches. Never raises."""
    data_path, ct_path, _miss = _paths(url)
    try:
        if not os.path.isfile(data_path):
            return None
        if (time.time() - os.path.getmtime(data_path)) > _ttl_seconds():
            _remove(data_path)
            _remove(ct_path)
            return None
        with open(data_path, "rb") as f:
            body = f.read()
        ctype = "image/x-icon"
        if os.path.isfile(ct_path):
            with open(ct_path, encoding="utf-8") as f:
                ctype = (f.read() or "image/x-icon").strip()
        return (body, ctype) if body else None
    except OSError as e:
        print(f"[favicon] cache read failed: {e}")
        return None


def is_miss(url: str) -> bool:
    """True when a fresh negative-cache marker says this site has no favicon —
    skips the upstream fetch. Stale markers are removed so it re-checks."""
    _data, _ct, miss_path = _paths(url)
    try:
        if not os.path.isfile(miss_path):
            return False
        if (time.time() - os.path.getmtime(miss_path)) > _MISS_TTL_SECONDS:
            _remove(miss_path)
            return False
        return True
    except OSError:
        return False


def put(url: str, data: bytes, content_type: str) -> None:
    """Cache a favicon's bytes + content type (atomic; best-effort no-op on
    empty data / write error)."""
    if not data:
        return
    ensure_dir()
    data_path, ct_path, miss_path = _paths(url)
    try:
        _atomic_write(data_path, data)
        _atomic_write(ct_path, (content_type or "image/x-icon").encode())
        _remove(miss_path)
    except OSError as e:
        print(f"[favicon] cache write failed: {e}")


def put_miss(url: str) -> None:
    """Write the negative-cache marker (site has no resolvable favicon)."""
    ensure_dir()
    _data, _ct, miss_path = _paths(url)
    try:
        _atomic_write(miss_path, b"")
    except OSError as e:
        print(f"[favicon] miss-marker write failed: {e}")


def _atomic_write(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# --- favicon fetch ---------------------------------------------------------

# Magic-byte prefixes for the formats a favicon can be: PNG, ICO, CUR,
# GIF (87a / 89a), JPEG, BMP. SVG + RIFF-WEBP are checked separately below.
_IMG_MAGIC = (
    b"\x89PNG\r\n\x1a\n",
    b"\x00\x00\x01\x00",
    b"\x00\x00\x02\x00",
    b"GIF87a",
    b"GIF89a",
    b"\xff\xd8\xff",
    b"BM",
)


def _looks_like_image(body: bytes, ctype: str) -> bool:
    """True when the bytes are plausibly an image — content-type ``image/*`` OR
    a recognised magic-byte prefix OR an SVG / RIFF-WEBP shape. Defends against
    a server that returns its HTML 200 shell (or octet-stream) for the favicon
    path."""
    if ctype.startswith("image/"):
        return True
    if not body:
        return False
    if body.startswith(_IMG_MAGIC):
        return True
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return True
    head = body[:256].lstrip().lower()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in body[:1024].lower())


_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(
    r"""(?P<key>[\w:-]+)\s*=\s*"""
    r"""(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<uq>[^\s">]+))""", re.IGNORECASE)


def _parse_icon_hrefs(html: str) -> list:
    """Extract ``href`` values from ``<link rel="...icon...">`` tags in the
    page head, ordered by preference (apple-touch / explicit icon first). Caps
    the parsed span so a huge page can't blow up the regex."""
    rated: list = []
    for tag in _LINK_TAG_RE.findall(html[:200000]):
        attrs: dict = {}
        for m in _ATTR_RE.finditer(tag):
            key = (m.group("key") or "").lower()
            val = m.group("dq") or m.group("sq") or m.group("uq") or ""
            attrs[key] = val
        rel = (attrs.get("rel") or "").lower()
        href = (attrs.get("href") or "").strip()
        if href and "icon" in rel:
            # Prefer apple-touch-icon (usually a clean PNG), then a plain icon,
            # then shortcut-icon last.
            rank = 0 if "apple-touch" in rel else (1 if rel.strip() == "icon" else 2)
            rated.append((rank, href))
    rated.sort(key=lambda t: t[0])
    return [h for (_r, h) in rated]


async def fetch_favicon(
    url: str,
    *,
    allow_host: Callable[[str], Awaitable[bool]],
) -> "tuple[bytes, str] | None":
    """Fetch the best favicon for ``url`` — ``<scheme>://host/favicon.ico``
    first, then the ``<link rel=icon>`` declared in the page head. Returns
    ``(bytes, content_type)`` or ``None`` when nothing usable resolves.

    ``allow_host(host)`` is the async SSRF gate the route supplies (public host
    OR operator-registered internal host); it is re-checked for EVERY host this
    function is about to hit. Redirects are NOT followed (a 30x off the
    validated host can't smuggle the fetch onto an internal IP). Never raises.
    """
    import httpx  # noqa: PLC0415
    from urllib.parse import urljoin, urlsplit  # noqa: PLC0415

    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    timeout = _timeout_seconds()
    headers = {"User-Agent": "OmniGrid-favicon/1.0", "Accept": "image/*,*/*"}

    async def _try(tgt: str) -> "tuple[bytes, str] | None":
        try:
            thost = (urlsplit(tgt).hostname or "").lower()
        except (ValueError, TypeError):
            return None
        if not thost or not await allow_host(thost):
            return None
        try:
            # noinspection PyArgumentEqualDefault
            async with httpx.AsyncClient(timeout=timeout,
                                         follow_redirects=False) as cli:
                r = await cli.get(tgt, headers=headers)
        except (httpx.HTTPError, OSError):
            return None
        if r.status_code != 200:
            return None
        body = r.content or b""
        if not body or len(body) > _MAX_BYTES:
            return None
        ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        if not _looks_like_image(body, ctype):
            return None
        if not ctype.startswith("image/"):
            ctype = "image/x-icon"
        return body, ctype

    # 1) The conventional /favicon.ico at the site root.
    hit = await _try(origin + "/favicon.ico")
    if hit is not None:
        return hit

    # 2) Parse the page head for a declared <link rel="icon">.
    try:
        phost = (parts.hostname or "").lower()
        if phost and await allow_host(phost):
            # noinspection PyArgumentEqualDefault
            async with httpx.AsyncClient(timeout=timeout,
                                         follow_redirects=False) as pcli:
                pr = await pcli.get(url, headers={"User-Agent": headers["User-Agent"],
                                                  "Accept": "text/html,*/*"})
            pr_ct = (pr.headers.get("content-type") or "").lower()
            if pr.status_code == 200 and pr_ct.startswith(("text/html", "application/xhtml")):
                for href in _parse_icon_hrefs(pr.text)[:4]:
                    target = href if "://" in href else urljoin(origin + "/", href)
                    hit = await _try(target)
                    if hit is not None:
                        return hit
    except (httpx.HTTPError, OSError):
        return None
    return None
