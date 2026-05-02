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
