"""Shared URL safety check used by every probe module.

OmniGrid's outbound HTTP probes (Webmin, Pulse, Beszel, the asset
inventory token endpoint, etc.) all call ``httpx.AsyncClient`` with a
``base_url`` that comes from the admin-only ``/api/settings`` endpoint
(``require_admin`` gate + CSRF for cookie callers, write-only ``_set``
flag pattern for secrets). The URL is operator-set, NOT public input —
classic SSRF threat-model considerations don't apply because:

* Only authenticated admins can write the setting.
* An admin attacker already has direct access to the host network
  (OmniGrid runs on a Swarm manager); redirecting probes at internal
  IPs gains them no privilege over the access they already hold.
* The probe targets are operator-owned home-lab gear that often lives
  on RFC1918 / link-local space — refusing internal IPs would break
  the legitimate use case.

That said, CodeQL's ``py/full-ssrf`` rule still flags every call site
because the URL string flows from a settings field. ``is_safe_http_url``
gives every module ONE place to (a) reject obviously-broken inputs
(file://, javascript:, data:, missing hostname) and (b) point CodeQL
suppression annotations at a documented rationale. Keep the check
intentionally narrow — operators legitimately use http (LAN-only),
non-standard ports, raw IPs, and self-signed TLS. Anything stricter
would create false-negative friction for the home-lab deploy story.
"""
from __future__ import annotations

_ALLOWED_SCHEMES = ("http://", "https://")


def is_safe_http_url(url: str) -> bool:
    """Return True when ``url`` is a non-empty http/https URL with a host.

    Used by every probe module's entry point to short-circuit before
    any ``httpx`` call. The validator is INTENTIONALLY permissive: it
    rejects scheme typos and missing hostnames but accepts every
    legitimate operator-set value (LAN IPs, custom ports, self-signed
    HTTPS, paths, query strings). Strict allowlisting would break the
    home-lab deploy story.
    """
    if not url or not isinstance(url, str):
        return False
    s = url.strip().lower()
    if not s.startswith(_ALLOWED_SCHEMES):
        return False
    rest = s.split("://", 1)[1] if "://" in s else ""
    host = rest.split("/", 1)[0].split("?", 1)[0]
    return bool(host)


def _ip_is_public(ip_str: str) -> bool:
    """True only for a globally-routable unicast IP — rejects loopback,
    RFC1918 private, link-local (incl. the 169.254.169.254 cloud-metadata
    endpoint), ULA, reserved, multicast and unspecified addresses."""
    import ipaddress  # noqa: PLC0415
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return bool(
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_reserved
        and not ip.is_multicast
        and not ip.is_unspecified
    )


def host_resolves_public(host: str) -> bool:
    """True only when EVERY address ``host`` resolves to is a globally-routable
    PUBLIC IP. False for loopback / RFC1918 / link-local (169.254 metadata) /
    reserved / unresolved / mixed (any private answer fails the whole host).

    This is the SSRF gate for the favicon proxy — the one outbound fetch whose
    target is genuinely user-influenced (a bookmark / app URL). A private or
    internal target is refused here unless the caller separately allow-lists it
    (the operator's curated ``hosts_config`` hosts). BLOCKING ``getaddrinfo`` —
    callers in an async context MUST wrap this in ``asyncio.to_thread``.

    Resolving ALL answers + requiring every one public defuses the classic
    DNS-rebinding trick (a name that returns one public + one private A record).
    """
    import socket  # noqa: PLC0415
    h = (host or "").strip().lower()
    if not h:
        return False
    # Strip an IPv6 literal's brackets, if present.
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    # IP literal — check directly, no DNS.
    try:
        import ipaddress  # noqa: PLC0415
        ipaddress.ip_address(h)
        return _ip_is_public(h)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(h, None)
    except (socket.gaierror, OSError, UnicodeError):
        return False
    addrs = {ai[4][0] for ai in infos}
    if not addrs:
        return False
    return all(_ip_is_public(a) for a in addrs)
