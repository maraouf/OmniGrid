"""Remote-registry manifest digest checking.

Parses image references, talks to Docker-Distribution-v2 registries with
Bearer-token auth, caches tokens per (realm, service, scope), and reports
latency / error counters to ``logic.metrics``.

No internal OmniGrid state beyond the token cache — safe to extract
as a leaf module.
"""
import os
import time
from typing import Optional

import httpx

from logic import metrics


DOCKERHUB_USER = os.getenv("DOCKERHUB_USER", "")
DOCKERHUB_TOKEN = os.getenv("DOCKERHUB_TOKEN", "")


# Bearer tokens keyed by (realm | service | scope). Each entry is
# (token, expires_at_epoch_seconds). Expiry is `expires_in - 30s` so we
# rotate slightly before the server's clock says they're dead.
_token_cache: dict[str, tuple[str, float]] = {}


def parse_image_ref(ref: str) -> tuple[str, str, str]:
    """Return (registry, repo, tag) from an image reference.

    - ``nginx`` → ("registry-1.docker.io", "library/nginx", "latest")
    - ``ghcr.io/foo/bar:1.2`` → ("ghcr.io", "foo/bar", "1.2")
    - ``lscr.io/linuxserver/plex:latest@sha256:…`` — digest is stripped.
    """
    if "@" in ref:
        ref = ref.split("@", 1)[0]
    parts = ref.split("/", 1)
    first = parts[0]
    is_reg = "." in first or ":" in first or first == "localhost"
    if is_reg and len(parts) == 2:
        registry, repo = first, parts[1]
    else:
        registry = "registry-1.docker.io"
        repo = ref if "/" in ref else f"library/{ref}"
    if ":" in repo.rsplit("/", 1)[-1]:
        repo, tag = repo.rsplit(":", 1)
    else:
        tag = "latest"
    return registry, repo, tag


def hub_link(image: str) -> Optional[str]:
    """Return a user-browsable link for the image's repo tags page.

    Best-effort — known registries only. Falls back to None for private
    registries and anything not on this list.
    """
    try:
        reg, repo, _ = parse_image_ref(image)
    except Exception:
        return None
    if reg == "lscr.io" and repo.startswith("linuxserver/"):
        return f"https://github.com/linuxserver/docker-{repo.split('/', 1)[1]}"
    if reg == "ghcr.io":
        return f"https://github.com/{repo}"
    if reg == "registry-1.docker.io":
        if repo.startswith("library/"):
            return f"https://hub.docker.com/_/{repo.split('/', 1)[1]}/tags"
        return f"https://hub.docker.com/r/{repo}/tags"
    return None


def tag_of(image: str) -> str:
    """Cheaper variant of parse_image_ref()[2] for the UI — doesn't
    validate, just returns whatever's after the last colon in the tail
    segment. 'latest' when absent."""
    last = image.split("/")[-1]
    return last.rsplit(":", 1)[1] if ":" in last else "latest"


async def _get_bearer(client: httpx.AsyncClient, www_auth: str, repo: str) -> Optional[str]:
    """Exchange a ``WWW-Authenticate: Bearer ...`` challenge for a token.

    Parses realm / service / scope out of the challenge, looks up cached
    token, otherwise hits the realm and caches the result. Docker Hub
    requests use DOCKERHUB_USER/TOKEN when set (avoids anonymous rate
    limits). Anything else is anonymous — private registries need
    credentials wired per-image, not yet supported.
    """
    if not www_auth.lower().startswith("bearer "):
        return None
    params: dict[str, str] = {}
    for part in www_auth[7:].split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            params[k.strip()] = v.strip().strip('"')
    realm = params.get("realm")
    if not realm:
        return None
    service = params.get("service", "")
    scope = params.get("scope", f"repository:{repo}:pull")
    key = f"{realm}|{service}|{scope}"
    if key in _token_cache:
        t, exp = _token_cache[key]
        if exp > time.time():
            return t
    auth = None
    if "docker.io" in realm and DOCKERHUB_USER and DOCKERHUB_TOKEN:
        auth = (DOCKERHUB_USER, DOCKERHUB_TOKEN)
    try:
        r = await client.get(realm, params={"service": service, "scope": scope}, auth=auth)
        r.raise_for_status()
        j = r.json()
        tok = j.get("token") or j.get("access_token")
        if tok:
            _token_cache[key] = (tok, time.time() + int(j.get("expires_in", 300)) - 30)
        return tok
    except Exception as e:
        print(f"[auth] {e}")
        return None


async def get_remote_digest(client: httpx.AsyncClient, image: str) -> Optional[str]:
    """HEAD (fallback GET) the registry's manifest endpoint and return the
    ``Docker-Content-Digest`` header.

    Records per-registry latency and error counters for Prometheus.
    """
    # Parse OUTSIDE the timed block — we need the registry host for the
    # histogram label, and we shouldn't charge parse-only failures to
    # registry latency.
    try:
        reg, repo, tag = parse_image_ref(image)
    except Exception as e:
        print(f"[digest] parse {image}: {e}")
        return None
    _t0 = time.monotonic()
    digest: Optional[str] = None
    try:
        accept = ", ".join([
            "application/vnd.docker.distribution.manifest.v2+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.oci.image.manifest.v1+json",
            "application/vnd.oci.image.index.v1+json",
        ])
        url = f"https://{reg}/v2/{repo}/manifests/{tag}"
        h = {"Accept": accept}
        r = await client.head(url, headers=h, follow_redirects=True)
        if r.status_code == 401:
            tok = await _get_bearer(client, r.headers.get("www-authenticate", ""), repo)
            if tok:
                h["Authorization"] = f"Bearer {tok}"
                r = await client.head(url, headers=h, follow_redirects=True)
        if r.status_code == 200:
            digest = r.headers.get("docker-content-digest")
        elif r.status_code in (404, 405):
            r = await client.get(url, headers=h, follow_redirects=True)
            if r.status_code == 200:
                digest = r.headers.get("docker-content-digest")
        if digest is None:
            metrics.REGISTRY_ERRORS.labels(registry=reg).inc()
        return digest
    except Exception as e:
        metrics.REGISTRY_ERRORS.labels(registry=reg).inc()
        print(f"[digest] {image}: {e}")
        return None
    finally:
        metrics.REGISTRY_LATENCY.labels(registry=reg).observe(time.monotonic() - _t0)
