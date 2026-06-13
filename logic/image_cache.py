"""Disk cache for the image proxies.

Both image-proxy routes fetch a remote image SERVER-SIDE and stream the bytes
back to the browser:

  * the TMDB proxy ``GET /api/image-proxy?url=...`` (``main_pkg/scan_routes.py``)
  * the per-app proxy ``GET /api/services/{host}/{idx}/image-proxy?path=...``
    (``main_pkg/apps_routes.py``)

Without a cache, the same picture is re-downloaded from upstream on EVERY view —
wasteful when the same poster / avatar shows up across multiple providers /
cards (e.g. a TMDB poster a movie has in Radarr AND Seerr AND Tracearr, all
pointing at the identical ``image.tmdb.org`` URL). This module caches the
fetched bytes to disk keyed by the FETCH IDENTITY (upstream URL + an optional
NON-SENSITIVE per-chip cache tag), so a public-CDN image dedups across every
provider that references it, while an authenticated per-chip image stays
per-chip. The cache tag is a coarse discriminator like ``"<host_id>:<idx>"`` —
the raw credential is NEVER hashed (a SHA-256 of a secret is still a weak
treatment of sensitive data; the tag avoids touching the secret at all).

Layout: ``<data_dir>/image_cache/<sha256>`` holds the raw bytes (its mtime is
the cache time, for the TTL check); ``<sha256>.ct`` holds the content-type.
Atomic writes via ``.tmp`` + ``os.replace``. The cache is BEST-EFFORT — every
operation swallows OSError and degrades to "just fetch upstream", so a
read-only / full disk never breaks an image load.

Tunables (operator-managed, per-use reads — no restart needed):
  * ``tuning_image_proxy_cache_ttl_seconds`` — how long a cached image is served
    before re-fetching. ``0`` disables the cache entirely. Default 7 days
    (posters / avatars are effectively immutable; a content change is rare and
    a stale week is harmless).
  * ``tuning_image_proxy_cache_max_entries`` — hard cap on cached images; the
    oldest are pruned past it so the cache can't grow unbounded. ``0`` = no cap.
"""
from __future__ import annotations

import hashlib
import os
import random
import time

from logic.db import DB_PATH
from logic.tuning import Tunable, tuning_int

# Same data-volume derivation as logic/backups.py — the cache lives on the
# bind-mounted /app/data so it survives a container restart. Falls back to /tmp
# in config-error mode (DB_PATH unset) so importing this never crashes.
_DATA_DIR = os.path.dirname(DB_PATH) or "." if DB_PATH else "/tmp"
CACHE_DIR = os.path.join(_DATA_DIR, "image_cache")

# Probability of an opportunistic prune sweep on a cache write — keeps the
# directory bounded without a dedicated lifespan task (the cache is best-effort
# and self-healing, so an exact cadence isn't needed).
_PRUNE_PROBABILITY = 0.03


def _ttl() -> int:
    """Image-proxy cache freshness window in seconds (tunable)."""
    return tuning_int(Tunable.IMAGE_PROXY_CACHE_TTL_SECONDS)


def _max_entries() -> int:
    """Max image-proxy cache entries before a prune sweep (tunable)."""
    return tuning_int(Tunable.IMAGE_PROXY_CACHE_MAX_ENTRIES)


def ensure_dir() -> None:
    """Best-effort create of the cache directory."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
    except OSError:
        pass


def _key(url: str, cache_tag: str = "") -> str:
    """Cache key = sha256(upstream-URL + NON-SENSITIVE cache tag). Public images
    (no tag) collapse to the URL alone so the SAME picture dedups across every
    provider; an authenticated per-chip fetch stays distinct via its coarse
    ``"<host_id>:<idx>"`` tag. The raw credential is intentionally NEVER part of
    the hash input — hashing a secret is still a weak treatment of sensitive
    data (CodeQL ``py/weak-sensitive-data-hashing``); the tag is a public
    discriminator, not the secret."""
    return hashlib.sha256(
        (str(url) + "\x00" + str(cache_tag)).encode(errors="replace")
    ).hexdigest()


def get(url: str, cache_tag: str = "") -> "tuple[bytes, str] | None":
    """Return ``(bytes, content_type)`` for a still-fresh cached image, else
    ``None`` (cache disabled / miss / stale / read error)."""
    ttl = _ttl()
    if ttl <= 0:
        return None
    data_path = os.path.join(CACHE_DIR, _key(url, cache_tag))
    try:
        st = os.stat(data_path)
    except OSError:
        return None
    if (time.time() - st.st_mtime) >= ttl:
        return None  # stale — left in place for the next prune
    try:
        with open(data_path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    ct = "image/jpeg"
    try:
        with open(data_path + ".ct", encoding="utf-8") as f:
            ct = (f.read().strip() or "image/jpeg")
    except OSError:
        pass
    return data, ct


def put(url: str, data: bytes, content_type: str,
        cache_tag: str = "") -> None:
    """Cache an image's bytes + content-type (best-effort; no-op when the cache
    is disabled or the data is empty). Atomic via ``.tmp`` + ``os.replace``."""
    if _ttl() <= 0 or not data:
        return
    ensure_dir()
    data_path = os.path.join(CACHE_DIR, _key(url, cache_tag))
    tmp = f"{data_path}.tmp{os.getpid()}"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, data_path)
        with open(data_path + ".ct", "w", encoding="utf-8") as f:
            f.write(content_type or "image/jpeg")
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return
    if random.random() < _PRUNE_PROBABILITY:
        prune()


def _remove(data_path: str) -> None:
    """Best-effort delete a cache entry's data + content-type files."""
    for x in (data_path, data_path + ".ct"):
        try:
            os.remove(x)
        except OSError:
            pass


def prune() -> int:
    """Delete expired entries + trim the cache to ``max_entries`` (oldest
    first). Returns the number of entries removed. Best-effort."""
    ttl = _ttl()
    maxn = _max_entries()
    try:
        names = [n for n in os.listdir(CACHE_DIR)
                 if not n.endswith(".ct") and ".tmp" not in n]
    except OSError:
        return 0
    now = time.time()
    removed = 0
    survivors: list[tuple[float, str]] = []
    for n in names:
        p = os.path.join(CACHE_DIR, n)
        try:
            mt = os.stat(p).st_mtime
        except OSError:
            continue
        if 0 < ttl <= (now - mt):
            _remove(p)
            removed += 1
            continue
        survivors.append((mt, p))
    if 0 < maxn < len(survivors):
        survivors.sort()  # oldest first
        for _mt, p in survivors[:len(survivors) - maxn]:
            _remove(p)
            removed += 1
    return removed
