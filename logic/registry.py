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


# Bounded set of registry-label values for `omnigrid_registry_errors_total`
# and `omnigrid_registry_latency_seconds`. Without this
# cap the label is the raw registry hostname and unbounded — an operator
# pulling from 50 private registries would inflate Prometheus cardinality
# proportionally. Known public registries map to themselves; everything
# else (private mirrors, self-hosted Harbor, etc.) collapses into a single
# `private` bucket. Add new public registries by appending to the set.
_KNOWN_REGISTRIES = frozenset({
    "registry-1.docker.io",  # canonical Docker Hub host
    "docker.io",             # also used as a label by some clients
    "ghcr.io",
    "gcr.io",
    "quay.io",
    "lscr.io",
    "mcr.microsoft.com",
    "public.ecr.aws",
})


def _classify_registry(host: str) -> str:
    """Bucket a registry hostname into one of the known label values.

    Empty / falsy → ``"unknown"``. Hostnames in ``_KNOWN_REGISTRIES`` →
    themselves (with `registry-1.docker.io` collapsed to `docker.io` for
    operator readability). Everything else → ``"private"``.
    """
    if not host:
        return "unknown"
    h = host.strip().lower()
    if h == "registry-1.docker.io":
        return "docker.io"
    if h in _KNOWN_REGISTRIES:
        return h
    return "private"


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
    # CodeQL py/incomplete-url-substring-sanitization: the previous
    # `"docker.io" in realm` substring check would match a malicious
    # `realm` like `https://attacker.example/docker.io/token` and send
    # the Dockerhub credentials to the attacker's host. Switch to a
    # proper hostname parse + exact suffix match so the check only
    # passes for `auth.docker.io` (the canonical Dockerhub auth realm)
    # and any `*.docker.io` subdomain Docker may rotate to in future.
    try:
        from urllib.parse import urlparse as _urlparse
        _host = (_urlparse(realm).hostname or "").lower()
    except Exception:
        _host = ""
    _is_dockerhub = (_host == "docker.io" or _host.endswith(".docker.io"))
    if _is_dockerhub and DOCKERHUB_USER and DOCKERHUB_TOKEN:
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


# Release-notes cache. Keyed by `image` (full ref). Value:
# {"ts": float, "data": dict}. Notes don't change after publish so TTL is
# long (24h); cache survives across pulls. Short failure-TTL (10 min)
# prevents hammering when GitHub rate-limits.
_release_notes_cache: dict[str, dict] = {}
_RELEASE_NOTES_TTL_OK_S = 24 * 3600
_RELEASE_NOTES_TTL_ERR_S = 10 * 60


async def _fetch_image_config_labels(
    client: httpx.AsyncClient, image: str,
) -> dict[str, str]:
    """Pull the OCI image config blob's `config.Labels` for one image.

    Walks the registry's manifest → manifest-list (arch-pick) → config
    blob chain. Returns ``{label: value}`` (typically the
    ``org.opencontainers.image.*`` family). Empty dict on any failure
    or when the registry doesn't expose labels.
    """
    try:
        reg, repo, tag = parse_image_ref(image)
    except Exception:
        return {}
    accept = ", ".join([
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.oci.image.index.v1+json",
    ])
    h: dict[str, str] = {"Accept": accept}
    try:
        url = f"https://{reg}/v2/{repo}/manifests/{tag}"
        r = await client.get(url, headers=h, follow_redirects=True)
        if r.status_code == 401:
            tok = await _get_bearer(client, r.headers.get("www-authenticate", ""), repo)
            if tok:
                h["Authorization"] = f"Bearer {tok}"
                r = await client.get(url, headers=h, follow_redirects=True)
        if r.status_code != 200:
            return {}
        manifest = r.json()
        # If this is a manifest-index (multi-arch), pick the linux/amd64
        # variant (first amd64 entry). Operators on arm64 hosts still
        # have amd64 labels — they're identical across arch sub-manifests
        # by convention. Defensive fallback to first entry if no amd64.
        if "manifests" in manifest:
            picks = [m for m in manifest["manifests"]
                     if (m.get("platform") or {}).get("architecture") == "amd64"]
            sub = picks[0] if picks else (manifest["manifests"][0] if manifest["manifests"] else None)
            if not sub:
                return {}
            sub_digest = sub.get("digest")
            if not sub_digest:
                return {}
            url2 = f"https://{reg}/v2/{repo}/manifests/{sub_digest}"
            r2 = await client.get(url2, headers=h, follow_redirects=True)
            if r2.status_code != 200:
                return {}
            manifest = r2.json()
        config = manifest.get("config") or {}
        config_digest = config.get("digest")
        if not config_digest:
            return {}
        blob_url = f"https://{reg}/v2/{repo}/blobs/{config_digest}"
        r3 = await client.get(blob_url, headers=h, follow_redirects=True)
        if r3.status_code != 200:
            return {}
        body = r3.json()
        # Labels can live under either `config.Labels` (Docker schema 2)
        # OR top-level `config.Labels` of the unwrapped config blob.
        labels = (body.get("config") or {}).get("Labels") or {}
        if not isinstance(labels, dict):
            return {}
        return {str(k): str(v) for k, v in labels.items() if v is not None}
    except Exception as e:
        print(f"[release-notes] config-labels {image}: {e}")
        return {}


def _parse_github_source(source_url: str) -> Optional[tuple[str, str]]:
    """Extract ``(owner, repo)`` from a GitHub source URL.

    Accepts both ``https://github.com/owner/repo`` and the trailing-slash /
    trailing-suffix variants (``.git`` / ``/tree/main`` / etc.).
    Returns None for any non-github.com URL.
    """
    try:
        from urllib.parse import urlparse
        u = urlparse(source_url)
        if (u.hostname or "").lower() != "github.com":
            return None
        parts = [p for p in (u.path or "").strip("/").split("/") if p]
        if len(parts) < 2:
            return None
        owner = parts[0]
        repo = parts[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        return owner, repo
    except Exception:
        return None


async def _fetch_github_release_notes(
    client: httpx.AsyncClient, owner: str, repo: str, tag: str,
) -> Optional[dict]:
    """Pull a GitHub release's notes for ``tag`` from the public API.

    Tries both the literal tag and a `v`-prefixed variant since many
    projects publish releases as ``v1.2.3`` while images are pinned at
    ``:1.2.3`` (or vice versa). Returns
    ``{name, body, html_url, published_at}`` or None.
    """
    candidates = [tag]
    if tag and not tag.startswith("v"):
        candidates.append("v" + tag)
    elif tag.startswith("v"):
        candidates.append(tag[1:])
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "OmniGrid",
    }
    # Operator-supplied GitHub token avoids the 60/hr anonymous rate
    # limit. Optional — anonymous calls work for public repos under
    # normal usage.
    gh_tok = os.getenv("GITHUB_TOKEN", "")
    if gh_tok:
        h["Authorization"] = f"Bearer {gh_tok}"
    for cand in candidates:
        try:
            url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{cand}"
            r = await client.get(url, headers=h, follow_redirects=True)
            if r.status_code == 200:
                body = r.json()
                return {
                    "name":         body.get("name") or cand,
                    "body":         body.get("body") or "",
                    "html_url":     body.get("html_url") or f"https://github.com/{owner}/{repo}/releases/tag/{cand}",
                    "published_at": body.get("published_at") or "",
                }
        except Exception as e:
            print(f"[release-notes] github {owner}/{repo}@{cand}: {e}")
            continue
    return None


async def get_release_notes(image: str) -> dict:
    """Best-effort release-notes lookup for an image.

    Resolution chain:
      1. Pull OCI labels from the registry — `org.opencontainers.image.source`
         and `org.opencontainers.image.version` are the canonical fields.
      2. Source URL → host detection. GitHub-hosted → GitHub Releases API.
         Other hosts → fall through to the source-link-only response.
      3. Tag selection — prefer the image's CURRENT tag (parsed from the
         ref) since that's what the operator is pulling; fall back to
         the OCI `version` label if the ref tag is `latest`.

    Returns a shape the SPA can render uniformly:
      ``{ok, source_url, source_host, tag, name, body, html_url, error}``
    """
    if not image:
        return {"ok": False, "error": "no image"}
    # Cache: long TTL for ok results, shorter for misses.
    cached = _release_notes_cache.get(image)
    if cached:
        age = time.time() - (cached.get("ts") or 0)
        ttl = _RELEASE_NOTES_TTL_OK_S if (cached.get("data") or {}).get("ok") else _RELEASE_NOTES_TTL_ERR_S
        if age < ttl:
            return cached["data"]
    try:
        reg, repo, ref_tag = parse_image_ref(image)
    except Exception as e:
        out = {"ok": False, "error": f"parse: {e}"}
        _release_notes_cache[image] = {"ts": time.time(), "data": out}
        return out
    async with httpx.AsyncClient(timeout=10.0) as client:
        labels = await _fetch_image_config_labels(client, image)
        source_url = (
            labels.get("org.opencontainers.image.source")
            or labels.get("org.opencontainers.image.url")
            or ""
        ).strip()
        version_label = (labels.get("org.opencontainers.image.version") or "").strip()
        # Choose the tag we ASK release-notes for. Prefer the ref's tag
        # (operator-visible "what they're pulling") unless it's the
        # rolling `latest` — then use the version label baked into the
        # image's config blob.
        tag = ref_tag if (ref_tag and ref_tag != "latest") else version_label
        if not source_url:
            out = {
                "ok": False,
                "error": "no source label on image",
                "source_url": "",
                "source_host": "",
                "tag": tag or ref_tag,
            }
            _release_notes_cache[image] = {"ts": time.time(), "data": out}
            return out
        # GitHub path (handles ghcr.io images whose source label points
        # at github.com).
        gh = _parse_github_source(source_url)
        if gh and tag:
            owner, gh_repo = gh
            release = await _fetch_github_release_notes(client, owner, gh_repo, tag)
            if release:
                out = {
                    "ok":          True,
                    "source_url":  source_url,
                    "source_host": "github.com",
                    "tag":         tag,
                    "name":        release["name"],
                    "body":        release["body"],
                    "html_url":    release["html_url"],
                    "published_at": release["published_at"],
                }
                _release_notes_cache[image] = {"ts": time.time(), "data": out}
                return out
        # Fallback — surface the source URL so the SPA can link out
        # even when the release-notes API didn't yield a body.
        out = {
            "ok":          False,
            "source_url":  source_url,
            "source_host": (source_url.split("/")[2] if "//" in source_url else ""),
            "tag":         tag or ref_tag,
            "error":       "no release notes found for tag" if tag else "no version tag resolved",
        }
        _release_notes_cache[image] = {"ts": time.time(), "data": out}
        return out


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
            metrics.REGISTRY_ERRORS.labels(registry=_classify_registry(reg)).inc()
        return digest
    except Exception as e:
        metrics.REGISTRY_ERRORS.labels(registry=_classify_registry(reg)).inc()
        print(f"[digest] {image}: {e}")
        return None
    finally:
        metrics.REGISTRY_LATENCY.labels(registry=_classify_registry(reg)).observe(time.monotonic() - _t0)
