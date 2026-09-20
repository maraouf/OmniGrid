"""Remote-registry manifest digest checking.

Parses image references, talks to Docker-Distribution-v2 registries with
Bearer-token auth, caches tokens per (realm, service, scope), and reports
latency / error counters to ``logic.metrics``.

No internal OmniGrid state beyond the token cache — safe to extract
as a leaf module.
"""
import asyncio
import contextlib
import time
from typing import Optional

import httpx

from logic import metrics
from logic.env_keys import EnvKey, env_get
from logic.external_urls import ExternalURL
from logic.url_safety import is_safe_http_url

DOCKERHUB_USER = env_get(EnvKey.DOCKERHUB_USER)
DOCKERHUB_TOKEN = env_get(EnvKey.DOCKERHUB_TOKEN)

# Bounded set of registry-label values for `omnigrid_registry_errors_total`
# and `omnigrid_registry_latency_seconds`. Without this
# cap the label is the raw registry hostname and unbounded — an operator
# pulling from 50 private registries would inflate Prometheus cardinality
# proportionally. Known public registries map to themselves; everything
# else (private mirrors, self-hosted Harbor, etc.) collapses into a single
# `private` bucket. Add new public registries by appending to the set.
_KNOWN_REGISTRIES = frozenset({
    ExternalURL.DOCKER_REGISTRY_HOST,  # canonical Docker Hub host
    ExternalURL.DOCKER_IO_HOST,  # also used as a label by some clients
    ExternalURL.GHCR_HOST,
    ExternalURL.GCR_HOST,
    ExternalURL.QUAY_HOST,
    ExternalURL.LSCR_HOST,
    ExternalURL.MCR_HOST,
    ExternalURL.ECR_PUBLIC_HOST,
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
    if h == ExternalURL.DOCKER_REGISTRY_HOST:
        return ExternalURL.DOCKER_IO_HOST
    if h in _KNOWN_REGISTRIES:
        return h
    return "private"


# Per-registry credentials, operator-managed in Admin → Registries and stored
# as the `registry_credentials` JSON setting. Before these existed, every
# non-Docker-Hub registry was probed anonymously: a private Forgejo / Harbor /
# GitLab registry answered the token request with 401, the digest never
# resolved, and the row went red with `status=error` — indistinguishable from a
# broken image, on a service that was running perfectly.
#
# Parsed form is cached against the RAW setting string: `get_setting` is
# already read-through-cached, but json.loads on every probe of every image in
# a gather is not free, and the PERF-07 rule asks for the PARSE to be cached
# rather than the string alone. A settings write changes the string, which
# misses this cache and re-parses — no invalidation hook needed.
_creds_cache: tuple[str, dict[str, tuple[str, str]]] = ("", {})


def _credentials_map() -> dict[str, tuple[str, str]]:
    """``{registry_host_lowercase: (username, password)}`` from the setting.

    Rows without a host or username are skipped, as are disabled ones. Never
    raises: a malformed setting degrades to anonymous probing, which is what
    the code did before credentials existed.
    """
    global _creds_cache
    try:
        from logic.db import get_setting  # noqa: PLC0415
        from logic.settings_keys import Settings  # noqa: PLC0415
        raw = get_setting(Settings.REGISTRY_CREDENTIALS) or ""
    except Exception:  # noqa: BLE001
        return {}
    if raw == _creds_cache[0]:
        return _creds_cache[1]
    out: dict[str, tuple[str, str]] = {}
    try:
        import json as _json  # noqa: PLC0415
        rows = _json.loads(raw) if raw.strip() else []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not row.get("enabled", True):
                continue
            host = str(row.get("host") or "").strip().lower()
            user = str(row.get("username") or "").strip()
            pw = str(row.get("password") or "")
            if host and user and pw:
                out[host] = (user, pw)
    except (TypeError, ValueError) as e:
        print(f"[auth] registry_credentials is not valid JSON, ignoring: {e}")
        out = {}
    _creds_cache = (raw, out)
    return out


# Credentials under test, injected by `_override_credentials` and consulted
# by `credentials_for` ahead of the stored map. Process-wide (the app is
# single-replica, single-process by design) and always unwound in a finally.
_creds_override: dict[str, tuple[str, str]] = {}


def credentials_for(registry_host: str) -> Optional[tuple[str, str]]:
    """Credentials for one registry hostname, or None.

    Matched on the EXACT hostname the image reference names, lowercased. No
    suffix or wildcard matching: a credential for `registry.example.com` must
    never travel to `evil-registry.example.com.attacker.test`, and the operator
    types the same host Docker pulls from, so exact is also what they expect.
    """
    if not registry_host:
        return None
    key = registry_host.strip().lower()
    # A credential under test wins over the stored one — see
    # `_override_credentials`.
    if key in _creds_override:
        return _creds_override[key]
    return _credentials_map().get(key)


async def probe_credentials(host: str, username: str, password: str,
                            repository: str = "") -> dict:
    """Check one registry credential. ``{ok, status, detail}``, never raises.

    With ``repository`` this runs the real digest probe, so a pass here is a
    pass on the next gather by construction. Without one it can only reach
    ``/v2/`` and follow its challenge, which proves the credential
    authenticates but not that it can pull that image.

    The credential under test is injected for the duration rather than saved,
    so an admin can verify BEFORE committing it — and a wrong one never
    reaches the stored map.
    """
    host = (host or "").strip().lower()
    if not host:
        return {"ok": False, "status": 0, "detail": "Registry host is required"}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            with _override_credentials(host, username, password):
                if repository:
                    repo_ref = repository.strip().strip("/")
                    tag = tag_of(repo_ref)
                    tail = repo_ref.split("/")[-1]
                    repo_path = repo_ref[:-(len(tag) + 1)] if ":" in tail else repo_ref
                    digest = await get_remote_digest(client, f"{host}/{repo_path}:{tag}")
                    if digest:
                        return {"ok": True, "status": 200,
                                "detail": f"OK — read {repo_path}:{tag} ({digest[:19]}…)"}
                    return {
                        "ok": False, "status": 0,
                        "detail": (f"No digest came back for {repo_path}:{tag} — check the "
                                   f"repository name, and that this account can pull it"),
                    }
                url = f"https://{host}/v2/"
                r = await client.get(url)
                if r.status_code == 200:
                    return {"ok": True, "status": 200,
                            "detail": "OK — this registry allows anonymous reads, "
                                      "so credentials are not needed for it"}
                if r.status_code != 401:
                    return {"ok": False, "status": r.status_code,
                            "detail": f"HTTP {r.status_code} from {url}"}
                challenge = r.headers.get("www-authenticate", "")
                if _wants_basic(challenge):
                    hdr = basic_auth_header(host) or ""
                    r2 = await client.get(url, headers={"Authorization": hdr})
                    ok, status = r2.status_code == 200, r2.status_code
                else:
                    tok = await _get_bearer(client, challenge, "", host)
                    ok, status = bool(tok), (200 if tok else 401)
        if ok:
            return {"ok": True, "status": 200,
                    "detail": ("OK — credentials accepted. Add a repository "
                               "(e.g. user/image:tag) to also verify pull access.")}
        return {"ok": False, "status": status,
                "detail": f"The registry rejected these credentials (HTTP {status})"}
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "detail": f"{type(e).__name__}: {e}"}


@contextlib.contextmanager
def _override_credentials(host: str, username: str, password: str):
    """Make `credentials_for(host)` answer with these for the duration.

    Lets the Test button exercise the SAME probe path a gather uses without
    first persisting a credential that may be wrong. Restores the previous
    entry (usually absent) on the way out, including on an exception.
    """
    key = (host or "").strip().lower()
    had = key in _creds_override
    prev = _creds_override.get(key)
    if key and username and password:
        _creds_override[key] = (username, password)
        # A token minted anonymously (or under other credentials), or a digest
        # resolved earlier, would otherwise be served from cache and report a
        # pass for a credential that was never exercised.
        _token_cache.clear()
        _drop_digest_cache_for(key)
    try:
        yield
    finally:
        if key:
            if had and prev is not None:
                _creds_override[key] = prev
            else:
                _creds_override.pop(key, None)
            _token_cache.clear()
            # Whatever the probe just resolved was read under the credential
            # being TESTED, not the stored one — don't leave it behind.
            _drop_digest_cache_for(key)


def _drop_digest_cache_for(registry_host: str) -> None:
    """Forget cached digests / platform maps for one registry host."""
    prefix = f"{registry_host}|"
    for cache in (_digest_cache, _platform_cache):
        for k in [k for k in cache if k.startswith(prefix)]:
            cache.pop(k, None)


def invalidate_auth_caches() -> None:
    """Forget every cached token and resolved digest.

    Called when the credentials change. The digest cache holds SUCCESSES
    only, but a stale success can still be wrong after a credential edit (a
    different account can see a different tag), and the token cache would
    otherwise keep using a token minted for the previous credential until it
    expired. Failures were never cached, so a newly-working credential is
    picked up on the next gather either way — this makes it immediate.
    """
    global _creds_cache
    _creds_cache = ("", {})
    _token_cache.clear()
    _digest_cache.clear()
    _platform_cache.clear()


def _wants_basic(www_auth: str) -> bool:
    """True when a 401's challenge asks for HTTP Basic rather than a token."""
    return (www_auth or "").strip().lower().startswith("basic ")


def basic_auth_header(registry_host: str) -> Optional[str]:
    """``Basic <base64>`` for a registry's stored credentials, else None."""
    creds = credentials_for(registry_host)
    if not creds:
        return None
    import base64  # noqa: PLC0415
    raw = f"{creds[0]}:{creds[1]}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


# Bearer tokens keyed by (realm | service | scope). Each entry is
# (token, expires_at_epoch_seconds). Expiry is `expires_in - 30s` so we
# rotate slightly before the server's clock says they're dead.
_token_cache: dict[str, tuple[str, float]] = {}

# result cache for resolved manifest digests, keyed on the parsed
# (registry|repo|tag) ref. get_remote_digest does one outbound HEAD per item
# per gather; a full gather fires 80+ HEADs (bounded by REGISTRY_CONCURRENCY),
# and gather re-runs on CACHE_TTL_SECONDS, on every write-op invalidation, AND
# on every auto-refresh / forced refresh (the SPA's auto-refresh tick uses
# ?force=true). Manifest digests change only on an upstream push, so a short
# TTL cache collapses repeated gathers — especially sub-TTL auto-refresh ticks
# — to "new-or-changed images only". Each entry is (digest, ts_monotonic).
# Cache SUCCESS ONLY: a transient registry failure (digest=None) must NOT pin
# "error" for the TTL. Deliberately NOT bypassed by ?force=true — the SPA's
# auto-refresh already sends force=true, so bypassing would re-HEAD every tick
# and defeat the cache; the time-based TTL is the freshness bound instead (an
# "update available" signal can lag by up to the TTL). A re-tag is still picked
# up because the tag is part of the key. TTL is the
# tuning_registry_digest_cache_ttl_seconds TUNABLE (0 disables the cache).
# Each entry is (digest, ts_wallclock). WALL-CLOCK (time.time()), not
# monotonic: the cache is PERSISTED to the ``registry_digest_cache`` table so
# a restart boots warm (seed_digest_cache_from_db / persist_digest_cache_to_db),
# and a monotonic timestamp is process-relative so it can't survive a restart.
# A wall-clock TTL is fine here — a backward NTP jump merely extends cache life
# slightly, a forward jump expires it slightly early; both re-HEAD cheaply.
_digest_cache: dict[str, tuple[str, float]] = {}

# Per-platform sub-manifest digests, keyed ``registry|repo|<index digest>``.
# Unlike _digest_cache this key names an IMMUTABLE object — an index digest is
# the hash of the index's own bytes, so what it contains can never change. The
# entry is still TTL'd (same knob) purely to bound the dict; correctness never
# depends on it expiring, which is why this cache is NOT persisted across a
# restart like _digest_cache is. Value is {"os/arch[/variant]": digest}.
_platform_cache: dict[str, tuple[dict[str, str], float]] = {}

# Docker reports a node's architecture with the uname spelling; OCI image
# indexes use the Go spelling. Comparing the two without this map means the
# platform lookup never matches and the whole index->sub-manifest check
# silently degrades to "cannot prove equal" — a fix that looks live and does
# nothing. Both directions of every pair OmniGrid can actually meet.
_ARCH_ALIASES: dict[str, str] = {
    "x86_64": "amd64", "x86-64": "amd64", "amd64": "amd64",
    "i386": "386", "i686": "386", "x86": "386",
    "aarch64": "arm64", "arm64": "arm64", "armv8l": "arm64",
    "armv7l": "arm", "armv6l": "arm", "arm": "arm",
    "ppc64le": "ppc64le", "s390x": "s390x", "riscv64": "riscv64",
}

# uname spellings that carry the ARM variant the OCI platform states
# separately. `armv7l` is `arm` + variant `v7`.
_ARCH_VARIANTS: dict[str, str] = {"armv7l": "v7", "armv6l": "v6", "armv8l": "v8"}

# Tags that are republished in place rather than pointing at one build for
# good. A digest resolved for one of these before a restart says what the tag
# meant then, not what it means now — see seed_digest_cache_from_db.
_MOVING_TAGS = frozenset({
    "latest", "main", "master", "edge", "nightly", "stable", "dev",
    "develop", "rolling", "unstable", "beta", "canary", "test",
})


def _tag_is_moving(cache_key: str) -> bool:
    """True when ``cache_key``'s tag is republished in place.

    The key is ``registry|repo|tag`` (see ``_get_remote_digest``). A tag that
    is only a major or major.minor line — ``3``, ``3.11``, ``v2`` — moves too:
    it follows its newest patch. A fully-qualified ``1.6.16`` does not.
    """
    tag = cache_key.rsplit("|", 1)[-1].strip().lower() if cache_key else ""
    if not tag or tag in _MOVING_TAGS:
        return True
    base = tag[1:] if tag.startswith("v") else tag
    # Two-or-fewer version parts is a line, not a release. Suffixed forms
    # (`1.6-alpine`) are left alone — that is a variant, not a claim of
    # immutability either way, and re-resolving is the safe reading.
    parts = base.split(".")
    if parts and all(p.isdigit() for p in parts):
        return len(parts) <= 2
    return False


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
        registry = ExternalURL.DOCKER_REGISTRY_HOST
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
    except (ValueError, AttributeError):
        return None
    if reg == ExternalURL.LSCR_HOST and repo.startswith("linuxserver/"):
        return f"{ExternalURL.GITHUB}/linuxserver/docker-{repo.split('/', 1)[1]}"
    if reg == ExternalURL.GHCR_HOST:
        return f"{ExternalURL.GITHUB}/{repo}"
    if reg == ExternalURL.DOCKER_REGISTRY_HOST:
        if repo.startswith("library/"):
            return f"{ExternalURL.DOCKER_HUB}/_/{repo.split('/', 1)[1]}/tags"
        return f"{ExternalURL.DOCKER_HUB}/r/{repo}/tags"
    return None


def tag_of(image: str) -> str:
    """Cheaper variant of parse_image_ref()[2] for the UI — doesn't
    validate, just returns whatever's after the last colon in the tail
    segment. 'latest' when absent."""
    last = image.split("/")[-1]
    return last.rsplit(":", 1)[1] if ":" in last else "latest"


async def _get_bearer(client: httpx.AsyncClient, www_auth: str, repo: str,
                      registry_host: str = "") -> Optional[str]:
    """Exchange a ``WWW-Authenticate: Bearer ...`` challenge for a token.

    Parses realm / service / scope out of the challenge, looks up cached
    token, otherwise hits the realm and caches the result. Docker Hub
    requests use DOCKERHUB_USER/TOKEN when set (avoids anonymous rate
    limits); any other registry uses the credentials the operator stored for
    ``registry_host`` in Admin → Registries, and stays anonymous without them.
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
    except (ValueError, ImportError):
        _host = ""
    _is_dockerhub = (_host == ExternalURL.DOCKER_IO_HOST or _host.endswith(".docker.io"))
    if _is_dockerhub and DOCKERHUB_USER and DOCKERHUB_TOKEN:
        auth = (DOCKERHUB_USER, DOCKERHUB_TOKEN)
    elif not _is_dockerhub:
        auth = credentials_for(registry_host)
    try:
        # Scopes to try, in order. The challenge's own scope comes first
        # because a registry that asks for something specific means it. Some
        # registries (Forgejo among them) answer the manifest 401 with the
        # catch-all `scope="*"` and then refuse to issue a token for it,
        # while the repository-scoped request every Docker client sends
        # succeeds — so fall back to that rather than giving up on a
        # credential that works.
        repo_scope = f"repository:{repo}:pull"
        scopes = [scope] if scope == repo_scope else [scope, repo_scope]
        last: Optional[Exception] = None
        for attempt in scopes:
            try:
                r = await client.get(realm, params={"service": service, "scope": attempt}, auth=auth)
                r.raise_for_status()
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as e:  # noqa: BLE001
                last = e
                continue
            j = r.json()
            tok = j.get("token") or j.get("access_token")
            if tok:
                _token_cache[f"{realm}|{service}|{attempt}"] = (
                    tok, time.time() + int(j.get("expires_in", 300)) - 30)
                if attempt != scope:
                    _token_cache[key] = _token_cache[f"{realm}|{service}|{attempt}"]
                return tok
        if last is not None:
            raise last
        return None
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e: # noqa: BLE001
        # Some httpx/SSL/auth exceptions stringify to empty — fall
        # back to the class name so the log line carries SOMETHING
        # actionable. Same pattern as the telegram_listener fix for
        # empty network: log bodies.
        body = str(e).strip() or e.__class__.__name__
        # Include the auth realm so operators can tell WHICH registry
        # rejected the token request at a glance — the bare class name
        # carries no context. `realm` is the variable bound to the
        # token-fetch URL above (e.g. `https://auth.docker.io/token`);
        # don't confuse with an undefined `url`.
        # Whether credentials were in play decides the operator's next move:
        # "anonymous" on a private registry means add them in Admin →
        # Registries, while a failure WITH credentials means the ones stored
        # are wrong or lack pull rights. The bare error said neither.
        _who = f"as {auth[0]!r}" if auth else (
            f"anonymously (no credentials stored for {registry_host!r})"
            if registry_host else "anonymously")
        print(f"[auth] registry-token fetch failed for realm={realm!r} "
              f"{_who}: {body}")
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
    except (ValueError, AttributeError):
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
        # SSRF defence in depth — `reg` / `repo` / `tag` were parsed
        # from an image reference attached to a Docker container that
        # the operator has running on their Swarm cluster. Threat model
        # is symmetric with `logic/url_safety.py`'s docstring: the
        # registry hostname is admin-controlled (operator deploys the
        # stack); RFC1918 / LAN-hosted private registries are a
        # LEGITIMATE home-lab use case. `is_safe_http_url()` rejects
        # broken inputs (file:// / javascript: / missing host) so the
        # CodeQL `py/full-ssrf` annotations on the two `client.get`
        # call sites below have a documented validator to cite.
        if not is_safe_http_url(url):
            return {}
        # codeql[py/full-ssrf] — gated by `is_safe_http_url(url)` above.
        r = await client.get(url, headers=h, follow_redirects=True)  # noqa: S310
        if r.status_code == 401:
            tok = await _get_bearer(client, r.headers.get("www-authenticate", ""), repo, reg)
            if tok:
                h["Authorization"] = f"Bearer {tok}"
                # codeql[py/full-ssrf] — same URL re-issued with bearer; gated above.
                r = await client.get(url, headers=h, follow_redirects=True)  # noqa: S310
        if r.status_code != 200:
            return {}
        manifest = r.json()

        # Shared sub-fetcher used for the manifest-index unwrap AND the
        # config-blob fetch — both follow the same "GET this URL, return
        # parsed JSON on 200, bail otherwise" pattern.
        async def _fetch_json_or_empty(sub_url: str) -> Optional[dict]:
            """GET `sub_url` with the prepared Accept+Auth headers; return JSON on 200, else None."""
            r2 = await client.get(sub_url, headers=h, follow_redirects=True)
            if r2.status_code != 200:
                return None
            return r2.json()

        # If this is a manifest-index (multi-arch), pick the linux/amd64
        # variant (first amd64 entry). Operators on arm64 hosts still
        # have amd64 labels — they're identical across arch sub-manifests
        # by convention. Defensive fallback to first entry if no amd64.
        if "manifests" in manifest:
            picks = [m for m in manifest["manifests"]
                     if (m.get("platform") or {}).get("architecture") == "amd64"]
            sub: Optional[dict] = picks[0] if picks else (
                manifest["manifests"][0] if manifest["manifests"] else None
            )
            if sub is None:
                return {}
            sub_digest = sub.get("digest")
            if not sub_digest:
                return {}
            _next = await _fetch_json_or_empty(
                f"https://{reg}/v2/{repo}/manifests/{sub_digest}")
            if _next is None:
                return {}
            manifest = _next
        config = manifest.get("config") or {}
        config_digest = config.get("digest")
        if not config_digest:
            return {}
        body = await _fetch_json_or_empty(
            f"https://{reg}/v2/{repo}/blobs/{config_digest}")
        if body is None:
            return {}
        # Labels can live under either `config.Labels` (Docker schema 2)
        # OR top-level `config.Labels` of the unwrapped config blob.
        labels = (body.get("config") or {}).get("Labels") or {}
        if not isinstance(labels, dict):
            return {}
        return {str(k): str(v) for k, v in labels.items() if v is not None}
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e: # noqa: BLE001
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
    except (ValueError, AttributeError, ImportError):
        return None


# Rolling / pseudo-tag values that DON'T identify a release. When the
# image's `org.opencontainers.image.version` label carries one of these,
# `get_release_notes` treats it as "no version" and falls through to the
# `/releases/latest` GitHub path. Pre-fix, netdata/netdata:latest (and
# every other rolling-tag image whose version label was itself "latest"
# or empty) silently fell to the source-link-only response.
_ROLLING_TAG_SENTINELS = frozenset({
    "latest", "edge", "nightly", "stable", "master", "main",
    "dev", "develop", "beta", "rc", "unstable", "rolling",
    "", "none",
})

# ---------------------------------------------------------------------
# Known-image → GitHub-repo fallback map.
#
# Some upstream projects publish to Docker Hub WITHOUT setting the
# canonical OCI labels (`org.opencontainers.image.source` /
# `org.opencontainers.image.version`). For those, the registry path of
# `get_release_notes` returns no source URL and the SPA's release-notes
# placeholder renders empty. This map provides a manual fallback so the
# resolver can still find the GitHub repo (and from there the per-tag
# release notes).
#
# Key shape: ``"<image-repo-path>"`` (lowercase, no registry host, no
# tag — exactly what ``parse_image_ref`` returns as ``repo``). For
# Docker Hub: ``"owner/name"``; for ghcr.io / quay.io repos: same shape.
# Value shape: ``("gh_owner", "gh_repo")`` — preserves the casing the
# user-facing GitHub release pages use (GitHub URLs are
# case-insensitive at the API layer but the redirect canonicalises to
# the registered casing, so respect it for the source-link display).
#
# Add new entries here when an operator reports "release notes missing
# for image X". Keep entries alphabetised by image-path key for stable
# diffs; the dict is small enough that linear scan is cheap.
_KNOWN_IMAGE_SOURCES: dict[str, tuple[str, str]] = {
    # Proxmox Pulse — github.com/rcourtman/Pulse, published to Docker
    # Hub as `rcourtman/pulse` without OCI source/version labels (the
    # upstream Dockerfile builds before the labels were a convention).
    # Release tags are `vX.Y.Z` upstream; the resolver already tries
    # both prefixed + bare-tag variants via `_fetch_github_release_notes`,
    # so the operator's image pin like `rcourtman/pulse:5.1.33` matches
    # https://github.com/rcourtman/Pulse/releases/tag/v5.1.33 cleanly.
    "rcourtman/pulse": ("rcourtman", "Pulse"),
}


def _docker_hub_to_github_guess(repo: str) -> Optional[tuple[str, str]]:
    """Heuristic: for a Docker Hub `owner/name` image with no OCI labels,
    GUESS the matching GitHub repo at the same `owner/name` path.

    Returns ``(owner, repo)`` tuple — the caller is responsible for
    verifying the GitHub repo actually exists before claiming a hit
    (a name collision between Docker Hub + GitHub is a real possibility,
    e.g. `library/redis` on Hub vs `redis/redis` on GitHub). Conservative
    by design: only fires for two-segment Docker Hub paths
    (`owner/name`) — official library images like `library/nginx`
    legitimately need a separate path (nginx/nginx, etc.) and aren't
    covered by the simple same-name guess.
    """
    if not repo or "/" not in repo:
        return None
    parts = repo.split("/")
    if len(parts) != 2:
        return None
    owner, name = parts
    if not owner or not name:
        return None
    # Skip the synthetic `library/` namespace Docker Hub uses for its
    # official images — the GitHub repo path almost never matches
    # `library/<name>`. Use the explicit `_KNOWN_IMAGE_SOURCES` map for
    # these cases instead.
    if owner.lower() == "library":
        return None
    return owner, name


async def _fetch_github_latest_release(
    client: httpx.AsyncClient, owner: str, repo: str,
) -> Optional[dict]:
    """Pull the `latest` published release from GitHub's API.

    Fallback for rolling-tag images (`:latest`, `:edge`, etc.) where the
    image's version label is itself a rolling pseudo-tag and there's no
    specific release to query. Returns the same shape as
    `_fetch_github_release_notes` so callers can branch uniformly.
    """
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "OmniGrid",
    }
    gh_tok = env_get(EnvKey.GITHUB_TOKEN)
    if gh_tok:
        h["Authorization"] = f"Bearer {gh_tok}"
    try:
        url = f"{ExternalURL.GITHUB_API}/repos/{owner}/{repo}/releases/latest"
        r = await client.get(url, headers=h, follow_redirects=True)
        if r.status_code == 200:
            body = r.json()
            tag = body.get("tag_name") or ""
            return {
                "name": body.get("name") or tag or "latest",
                "body": body.get("body") or "",
                "html_url": body.get("html_url") or f"{ExternalURL.GITHUB}/{owner}/{repo}/releases/latest",
                "published_at": body.get("published_at") or "",
                "tag": tag,
            }
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e: # noqa: BLE001
        print(f"[release-notes] github {owner}/{repo} latest: {e}")
    return None


# noinspection DuplicatedCode
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
    gh_tok = env_get(EnvKey.GITHUB_TOKEN)
    if gh_tok:
        h["Authorization"] = f"Bearer {gh_tok}"
    for cand in candidates:
        try:
            url = f"{ExternalURL.GITHUB_API}/repos/{owner}/{repo}/releases/tags/{cand}"
            r = await client.get(url, headers=h, follow_redirects=True)
            if r.status_code == 200:
                body = r.json()
                return {
                    "name": body.get("name") or cand,
                    "body": body.get("body") or "",
                    "html_url": body.get("html_url") or f"{ExternalURL.GITHUB}/{owner}/{repo}/releases/tag/{cand}",
                    "published_at": body.get("published_at") or "",
                }
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception as e: # noqa: BLE001
            print(f"[release-notes] github {owner}/{repo}@{cand}: {e}")
            continue
    return None


_release_notes_inflight: dict[str, asyncio.Future] = {}


async def get_release_notes(image: str) -> dict:
    """Single-flight front for `_get_release_notes_impl`.

    The background warm and a browser asking for the same image (a tab
    that loads while the warm is still running) would otherwise each run
    the full cold lookup — twice the GitHub calls against a 60-an-hour
    budget. A caller that arrives mid-lookup awaits the leader's result.
    Waiters `shield` the shared future so one cancelled waiter cannot
    cancel it for everyone else.
    """
    pending = _release_notes_inflight.get(image)
    if pending is not None:
        return await asyncio.shield(pending)
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    _release_notes_inflight[image] = fut
    try:
        out = await _get_release_notes_impl(image)
        if not fut.done():
            fut.set_result(out)
        return out
    except BaseException:
        # Waiters get the same generic miss the route would return; the
        # leader re-raises so cancellation still propagates.
        if not fut.done():
            fut.set_result({"ok": False, "error": "release-notes lookup failed"})
        raise
    finally:
        if _release_notes_inflight.get(image) is fut:
            _release_notes_inflight.pop(image, None)


async def _get_release_notes_impl(image: str) -> dict:
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
        _ts_raw = cached.get("ts")
        _ts = float(_ts_raw) if isinstance(_ts_raw, (int, float)) else 0.0
        age = time.time() - _ts
        _data_raw = cached.get("data")
        _data = _data_raw if isinstance(_data_raw, dict) else {}
        ttl = _RELEASE_NOTES_TTL_OK_S if _data.get("ok") else _RELEASE_NOTES_TTL_ERR_S
        if age < ttl:
            return _data
    try:
        reg, repo, ref_tag = parse_image_ref(image)
    except Exception as e: # noqa: BLE001
        # Log the full parse error server-side, but return a GENERIC error
        # to the caller — this dict is returned verbatim to the client by
        # the /api/registry/release-notes route, and the raw exception text
        # can carry internal detail (CodeQL py/stack-trace-exposure). The
        # SPA only gates on ok / body / source_url, never renders `error`.
        print(f"[release-notes] parse failed for {image!r}: {e}")
        out = {"ok": False, "error": "could not parse image reference"}
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
        # When the image's OCI labels DON'T carry a source URL, fall back
        # to the known-image map FIRST (curated entries for projects
        # whose published images predate the OCI-labels convention —
        # e.g. rcourtman/pulse → github.com/rcourtman/Pulse), then to
        # the Docker-Hub-owner/name heuristic (operator deploys lots of
        # owner/name images whose GitHub repo lives at the same path).
        # The heuristic is verified by the GitHub API call downstream —
        # a 404 from `_fetch_github_release_notes` + `_fetch_github_latest_release`
        # falls through cleanly without polluting the cache or emitting
        # a stale source_url to the SPA. `gh_fallback` becomes the
        # source-of-truth for the `_parse_github_source` skip below
        # when source_url stays empty.
        gh_fallback: Optional[tuple[str, str]] = None
        if not source_url:
            mapped = _KNOWN_IMAGE_SOURCES.get(repo.lower())
            if mapped is not None:
                gh_fallback = mapped
                source_url = f"{ExternalURL.GITHUB}/{mapped[0]}/{mapped[1]}"
            else:
                guess = _docker_hub_to_github_guess(repo)
                if guess is not None:
                    gh_fallback = guess
                    source_url = f"{ExternalURL.GITHUB}/{guess[0]}/{guess[1]}"

        # Identify "specific" vs "rolling" tag values. A tag is specific
        # when it points at a real release (e.g. `1.45.6`, `v2.0.0`,
        # `nginx-1.27`) — those query GitHub by exact tag. Rolling tags
        # (`latest`, `edge`, `nightly`, empty, etc.) fall through to the
        # `/releases/latest` GitHub fallback so rolling-tag images still
        # surface meaningful release notes.
        def _is_specific(t: str) -> bool:
            return bool(t) and t.lower() not in _ROLLING_TAG_SENTINELS

        # Choose the tag we ASK release-notes for. Prefer the ref's tag
        # (operator-visible "what they're pulling") when it's specific;
        # else try the version label; else "" → triggers latest fallback.
        if _is_specific(ref_tag):
            tag = ref_tag
        elif _is_specific(version_label):
            tag = version_label
        else:
            tag = ""
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
        # at github.com). Try the specific-tag lookup first when we have
        # a real version; on miss OR for rolling-tag images, fall through
        # to `/releases/latest` so we still return something useful. The
        # `gh_fallback` (from `_KNOWN_IMAGE_SOURCES` / Docker Hub
        # heuristic above) takes precedence when set so we don't double-
        # parse the synthetic source URL we just constructed.
        gh = gh_fallback or _parse_github_source(source_url)
        if gh:
            owner, gh_repo = gh
            release: Optional[dict] = None
            used_tag = tag
            if tag:
                release = await _fetch_github_release_notes(client, owner, gh_repo, tag)
            if release is None:
                latest = await _fetch_github_latest_release(client, owner, gh_repo)
                if latest:
                    release = latest
                    used_tag = latest.get("tag") or "latest"
            if release:
                out = {
                    "ok": True,
                    "source_url": source_url,
                    "source_host": "github.com",
                    "tag": used_tag,
                    "name": release["name"],
                    "body": release["body"],
                    "html_url": release["html_url"],
                    "published_at": release["published_at"],
                    # Flag whether the response came from the tagged
                    # lookup or the latest-release fallback. Lets the
                    # SPA surface "Latest release: X" prefix when the
                    # image was pinned at a rolling tag and we couldn't
                    # match a specific release.
                    "is_latest_fallback": (tag != used_tag or not tag),
                }
                _release_notes_cache[image] = {"ts": time.time(), "data": out}
                return out
        # Fallback — surface the source URL so the SPA can link out
        # even when the release-notes API didn't yield a body.
        out = {
            "ok": False,
            "source_url": source_url,
            "source_host": (source_url.split("/")[2] if "//" in source_url else ""),
            "tag": tag or ref_tag,
            "error": "no release notes found for tag" if tag else "no version tag resolved",
        }
        _release_notes_cache[image] = {"ts": time.time(), "data": out}
        return out


# Release-notes prewarm. An update is known the moment a gather sees the
# registry digest move — often hours before anyone opens the confirm
# dialog — so the lookup (registry manifest + config blob + one or two
# GitHub calls, 1-3 s cold) is done in the background then, and the
# dialog reads a warm cache.
#
# Keyed on the REMOTE DIGEST the notes were warmed against, which is what
# makes this safe on the GitHub budget (60 unauthenticated calls an hour):
# each (image, digest) pair is looked up once per process, whatever it
# returned. A failed lookup is NOT retried by the warm — a click still
# retries it on the normal 10-minute error TTL. A digest that moves again
# means upstream pushed again, so the cached notes describe the previous
# image and are dropped before re-warming.
_release_notes_warmed: dict[str, str] = {}
_release_notes_warm_inflight = False


def release_notes_to_warm(items: list[dict]) -> list[tuple[str, str]]:
    """``(image, remote_digest)`` pairs whose notes need warming.

    Only items with a live pending update — an up-to-date image has no
    confirm dialog to serve, and an offline orphan has no Update button.
    One entry per image even when several services share it. Empty while a
    warm is already running, so a gather doesn't spawn a task just to have
    it return at the single-flight check.
    """
    if _release_notes_warm_inflight:
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for it in items or []:
        if not isinstance(it, dict) or it.get("status") != "update":
            continue
        if it.get("health") == "offline":
            continue
        image = str(it.get("image") or "").strip()
        if not image or image in seen:
            continue
        seen.add(image)
        digest = str(it.get("remote_digest") or "")
        if _release_notes_warmed.get(image) == digest:
            continue
        out.append((image, digest))
    return out


async def warm_release_notes(targets: list[tuple[str, str]]) -> int:
    """Look up each target's notes into the cache. Sequential on purpose —
    this is background work, and one call at a time keeps it gentle on
    the registry and the GitHub rate limit. Returns how many were warmed.
    Single-flight: a second call while one runs is a no-op; the next
    gather hands it whatever is still unwarmed."""
    global _release_notes_warm_inflight
    if _release_notes_warm_inflight or not targets:
        return 0
    _release_notes_warm_inflight = True
    n = 0
    try:
        for image, digest in targets:
            prev = _release_notes_warmed.get(image)
            if prev is not None and prev != digest:
                _release_notes_cache.pop(image, None)
            try:
                await get_release_notes(image)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as e:  # noqa: BLE001
                print(f"[release-notes] warm skipped for {image!r}: {e}")
            _release_notes_warmed[image] = digest
            n += 1
    finally:
        _release_notes_warm_inflight = False
    if n:
        print(f"[release-notes] INFO warmed {n} pending-update image(s)")
    return n


# noinspection DuplicatedCode
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
    except Exception as e: # noqa: BLE001
        print(f"[digest] parse {image}: {e}")
        return None
    # serve a recently-resolved digest from the result cache (see
    # _digest_cache). Lazy import keeps registry.py a leaf at module load; the
    # value is read per-use (no module-import caching) per the no-static-config
    # contract. ttl<=0 disables the cache entirely.
    from logic.tuning import tuning_int, Tunable
    _ttl = tuning_int(Tunable.REGISTRY_DIGEST_CACHE_TTL_SECONDS)
    _ck = f"{reg}|{repo}|{tag}"
    if _ttl > 0:
        _hit = _digest_cache.get(_ck)
        if _hit is not None and (time.time() - _hit[1]) < _ttl:
            return _hit[0]
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
            _challenge = r.headers.get("www-authenticate", "")
            tok = await _get_bearer(client, _challenge, repo, reg)
            if tok:
                h["Authorization"] = f"Bearer {tok}"
                r = await client.head(url, headers=h, follow_redirects=True)
            else:
                # No bearer token. A registry that challenges with Basic
                # (plain `registry:2` with htpasswd, some Harbor setups)
                # issues no tokens at all, so the stored credentials go on
                # the request directly. httpx drops the header if a redirect
                # leaves this origin, so the credential can't follow a 30x
                # to somewhere else.
                _basic = basic_auth_header(reg) if _wants_basic(_challenge) else None
                if _basic:
                    h["Authorization"] = _basic
                    r = await client.head(url, headers=h, follow_redirects=True)
        if r.status_code == 200:
            digest = r.headers.get("docker-content-digest")
        elif r.status_code in (404, 405):
            r = await client.get(url, headers=h, follow_redirects=True)
            if r.status_code == 200:
                digest = r.headers.get("docker-content-digest")
        if digest is None:
            metrics.REGISTRY_ERRORS.labels(registry=_classify_registry(reg)).inc()
        elif _ttl > 0:
            # Cache SUCCESS only — never store a None (transient failure).
            _digest_cache[_ck] = (digest, time.time())
        return digest
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e: # noqa: BLE001
        metrics.REGISTRY_ERRORS.labels(registry=_classify_registry(reg)).inc()
        print(f"[digest] {image}: {e}")
        return None
    finally:
        metrics.REGISTRY_LATENCY.labels(registry=_classify_registry(reg)).observe(time.monotonic() - _t0)


def normalize_platform(os_name: str, arch: str, variant: str = "") -> str:
    """One canonical ``os/arch[/variant]`` string from either spelling.

    Docker's node description says ``x86_64`` / ``armv7l``; an OCI index says
    ``amd64`` / ``arm`` + ``v7``. Both arrive here and leave identical.
    """
    o = (os_name or "").strip().lower() or "linux"
    a = (arch or "").strip().lower()
    v = (variant or "").strip().lower()
    if not v:
        v = _ARCH_VARIANTS.get(a, "")
    a = _ARCH_ALIASES.get(a, a)
    if not a:
        return ""
    # arm64's `v8` carries no information — every arm64 image is v8, and the
    # two sides disagree about whether to write it (a Docker node says
    # `aarch64` with no variant, an OCI index says arm64 + v8, and a node
    # reporting `armv8l` says v8 against an index that may not). Stripping it
    # HERE makes both sides agree through one rule, rather than two tolerances
    # that each cover one direction and leave the other broken.
    if a == "arm64" and v == "v8":
        v = ""
    return f"{o}/{a}/{v}" if v else f"{o}/{a}"


async def _index_platform_map(client: "httpx.AsyncClient", reg: str, repo: str,
                              digest: str) -> Optional[dict[str, str]]:
    """``{platform: sub-manifest digest}`` for a multi-arch index, else None.

    None means "this digest is not an index" — a plain single-arch manifest has
    no per-platform chain to follow, and the caller must NOT read that as
    agreement.
    """
    ttl = _digest_cache_ttl()
    ck = f"{reg}|{repo}|{digest}"
    if ttl > 0:
        hit = _platform_cache.get(ck)
        if hit is not None and (time.time() - hit[1]) < ttl:
            return hit[0] or None
    accept = ", ".join([
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    ])
    url = f"https://{reg}/v2/{repo}/manifests/{digest}"
    h = {"Accept": accept}
    try:
        r = await client.get(url, headers=h, follow_redirects=True)
        if r.status_code == 401:
            tok = await _get_bearer(client, r.headers.get("www-authenticate", ""), repo, reg)
            if not tok:
                return None
            h["Authorization"] = f"Bearer {tok}"
            r = await client.get(url, headers=h, follow_redirects=True)
        if r.status_code != 200:
            return None
        body = r.json()
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[digest] index {repo}@{digest[:19]}: {e}")
        return None
    entries = body.get("manifests")
    if not isinstance(entries, list) or not entries:
        # A single-arch manifest answered instead (registries ignore an Accept
        # they cannot satisfy) — no chain to follow.
        return None
    out: dict[str, str] = {}
    for m in entries:
        if not isinstance(m, dict):
            continue
        plat = m.get("platform")
        if not isinstance(plat, dict):
            continue
        # Attestation / provenance entries carry os=unknown. They are exactly
        # the sub-manifests that churn without the image changing, so leaving
        # them in would reintroduce the false positive one level down.
        if str(plat.get("os") or "").lower() in ("", "unknown"):
            continue
        key = normalize_platform(plat.get("os", ""), plat.get("architecture", ""),
                                 plat.get("variant", ""))
        dig = m.get("digest")
        if key and isinstance(dig, str) and dig:
            # `key` came from normalize_platform, so an index writing
            # arm64 + v8 is already stored under the same string a node
            # reporting `aarch64` will ask for. No per-side alias needed.
            out[key] = dig
    if ttl > 0:
        _platform_cache[ck] = (out, time.time())
    return out or None


async def same_image_for_platforms(client: "httpx.AsyncClient", image: str,
                                   local_digest: str, remote_digest: str,
                                   platforms: list[str]) -> bool:
    """True when two DIFFERING index digests still name the same image on every
    platform this item actually runs on.

    A multi-arch tag's index digest moves whenever ANY architecture in it is
    republished — including ones this fleet does not run, and including the
    attestation sub-manifests that ride along with them. `caddy:2-alpine` moved
    exactly that way: arm/v6, arm/v7 and arm64/v8 were rebuilt, amd64 was
    untouched, and an amd64-only Swarm was told to update to bytes it was
    already running.

    Answers only the narrow question. Anything unproven — either side not an
    index, a platform missing from either map, no platforms known — returns
    False, leaving the plain digest comparison's "update" verdict standing. A
    false "up to date" hides a real update, which is the worse failure.
    """
    if not (local_digest and remote_digest and platforms):
        return False
    if local_digest == remote_digest:
        return True
    try:
        reg, repo, _tag = parse_image_ref(image)
    except Exception:  # noqa: BLE001
        return False
    local_map = await _index_platform_map(client, reg, repo, local_digest)
    if not local_map:
        return False
    remote_map = await _index_platform_map(client, reg, repo, remote_digest)
    if not remote_map:
        return False
    for plat in platforms:
        if not plat:
            return False
        lhs = local_map.get(plat)
        rhs = remote_map.get(plat)
        if not lhs or not rhs or lhs != rhs:
            return False
    return True


def _digest_cache_ttl() -> int:
    """Resolve the digest-cache TTL tunable (0 disables the cache). Per-use read
    (no module-import caching) per the no-static-config contract."""
    from logic.tuning import tuning_int, Tunable # noqa: PLC0415
    return tuning_int(Tunable.REGISTRY_DIGEST_CACHE_TTL_SECONDS)


def _prune_digest_cache_inmem(ttl: int) -> None:
    """Evict in-memory ``_digest_cache`` entries older than ``ttl`` so the dict
    stays bounded (it never self-evicts on lookup — a stale entry just misses).
    Called before a persist flush so the DB mirrors only live entries."""
    if ttl <= 0:
        return
    now = time.time()
    stale = [k for k, (_d, ts) in _digest_cache.items() if (now - ts) >= ttl]
    for k in stale:
        _digest_cache.pop(k, None)


def seed_digest_cache_from_db() -> int:
    """Warm the in-memory digest result cache from the ``registry_digest_cache``
    table at boot so the FIRST post-restart gather reuses recently-resolved
    manifest digests instead of re-HEADing every image. Returns the count
    loaded. Synchronous (small table) — call from the lifespan boot-seed path.
    Rows older than the current TTL are skipped (they would miss anyway)."""
    ttl = _digest_cache_ttl()
    if ttl <= 0:
        return 0
    from logic.db import db_conn # noqa: PLC0415
    now = time.time()
    loaded = 0
    skipped = 0
    try:
        with db_conn() as c:
            cur = c.execute("SELECT cache_key, digest, ts FROM registry_digest_cache")
            for key, digest, ts in cur.fetchall():
                if not digest or (now - float(ts)) >= ttl:
                    continue
                # A MOVING tag is exactly the thing that can have changed
                # while this process was not running, so a digest resolved
                # before the restart is not evidence about the tag now. The
                # restart is often BECAUSE it moved: OmniGrid deploying
                # itself pushes a new `:latest` and then rolls the container
                # that would have re-read it. Warming that entry made the app
                # report an update available for its own freshly-deployed
                # image, comparing the new container against the digest its
                # predecessor had resolved for the old one.
                #
                # Version-pinned tags cannot have moved, so they keep the
                # whole point of this cache — not re-HEADing every image on
                # the first gather after a restart.
                if _tag_is_moving(key):
                    skipped += 1
                    continue
                _digest_cache[key] = (digest, float(ts))
                loaded += 1
        if skipped:
            print(f"[digest] INFO boot warm skipped {skipped} moving-tag "
                  f"entr{'y' if skipped == 1 else 'ies'} (re-resolved on the "
                  f"first gather); loaded {loaded}")
    except Exception as e: # noqa: BLE001
        print(f"[digest] seed_digest_cache_from_db failed: {e}")
    return loaded


def persist_digest_cache_to_db() -> int:
    """Flush the in-memory digest result cache to the ``registry_digest_cache``
    table so a restart boots warm. UPSERTs every live entry + prunes rows older
    than the TTL. Returns the number of rows written. Synchronous (small table)
    — call via ``asyncio.to_thread`` at the end of a successful gather so a
    slow SQLite write never blocks the event loop."""
    ttl = _digest_cache_ttl()
    if ttl <= 0:
        return 0
    from logic.db import db_conn, prune_rows_older_than # noqa: PLC0415
    _prune_digest_cache_inmem(ttl)
    now = int(time.time())
    rows = [(k, d, int(ts)) for k, (d, ts) in _digest_cache.items()]
    try:
        if rows:
            with db_conn() as c:
                c.executemany(
                    "INSERT INTO registry_digest_cache (cache_key, digest, ts) "
                    "VALUES (?, ?, ?) "
                    "ON CONFLICT(cache_key) DO UPDATE SET "
                    "digest=excluded.digest, ts=excluded.ts",
                    rows,
                )
        # Prune stale rows via the chunked helper (per-chunk commit releases
        # the writer lock so a large prune never stalls other writers).
        prune_rows_older_than("registry_digest_cache", now - ttl)
    except Exception as e: # noqa: BLE001
        print(f"[digest] persist_digest_cache_to_db failed: {e}")
        return 0
    return len(rows)
