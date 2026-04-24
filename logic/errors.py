"""OmniGrid error-code catalog.

Every user-facing error returned from the backend SHOULD carry a code
from this module. The code is a stable, i18n-routable identifier the
frontend uses to look up the localised message via
``t('errors.<code>')``.

Reading the catalog: each error is a single block where its constant
NAME, CODE (OG####), and English MESSAGE appear together via the
``_define()`` helper. Adding a new error is three lines (block of
CONSTANT = _define("OG####", "message")). Duplicate codes are
rejected at import time so the bug never ships.

Three steps to add an error:

    1. Pick the next free code in the right numbering block.
    2. Add a ``CONSTANT = _define("OG####", "English message")``
       block below, next to its siblings.
    3. Add a matching key under ``errors.OG####`` in
       static/i18n/en.json (and any other translation file that has
       non-empty strings — English is the fallback for missing keys).

Numbering blocks (leave holes for growth — codes are cheap):

    OG0001..OG0099  — network / transport
    OG0100..OG0199  — authentication / authorization
    OG0200..OG0299  — upstream API envelope / response shape
    OG0300..OG0399  — asset inventory
    OG0400..OG0499  — portainer (reserved)
    OG0500..OG0599  — beszel / pulse / node-exporter / webmin (reserved)
    OG0600..OG0699  — scheduler / ops / history (reserved)
    OG0700..OG0799  — backup / cache / persistence (reserved)
    OG0800..OG0899  — auth / session / OIDC (reserved)
    OG0900..OG0999  — misc
"""
from __future__ import annotations

import socket
from dataclasses import dataclass, field
from typing import Optional


# Single source of truth: code -> message. Populated exclusively via
# the ``_define()`` helper below so duplicates are impossible (the
# helper raises on the second registration of the same code).
_REGISTRY: dict[str, str] = {}


def _define(code: str, message: str) -> str:
    """Register one error code with its default English message.

    Returns the code (a plain string) so callers assign it to a
    module-level constant: ``FOO = _define("OG9999", "foo message")``.

    Raises ``RuntimeError`` on duplicate code or malformed code —
    fires at import time, so the app won't start with a broken catalog.
    """
    if not isinstance(code, str) or not code.startswith("OG"):
        raise RuntimeError(
            f"logic.errors._define: code must start with 'OG', got {code!r}"
        )
    if code in _REGISTRY:
        raise RuntimeError(
            f"logic.errors._define: duplicate error code {code!r}"
        )
    if not message or not isinstance(message, str):
        raise RuntimeError(
            f"logic.errors._define: message for {code} must be a non-empty string"
        )
    _REGISTRY[code] = message
    return code


# ============================================================================
# OG0001..OG0099 — Network / transport
# ============================================================================

DNS_RESOLVE_FAILED = _define(
    "OG0001",
    "DNS resolution failed — the hostname could not be resolved. "
    "Check the URL, container DNS (/etc/resolv.conf), split-horizon "
    "DNS, or an outbound VPN.",
)

CONNECTION_REFUSED = _define(
    "OG0002",
    "Connection refused by the upstream host — check the port and "
    "that the upstream service is running.",
)

CONNECTION_TIMEOUT = _define(
    "OG0003",
    "Upstream request timed out.",
)

TLS_HANDSHAKE_FAILED = _define(
    "OG0004",
    "TLS handshake with the upstream failed — check the certificate "
    "or disable VERIFY_TLS for self-signed endpoints.",
)

NETWORK_ERROR = _define(
    "OG0099",
    "Network error reaching the upstream.",
)


# ============================================================================
# OG0100..OG0199 — Authentication / authorization
# ============================================================================

AUTH_TOKEN_REJECTED = _define(
    "OG0100",
    "Upstream rejected the credentials (401). Token may be expired, "
    "revoked, or minted under a different tenant.",
)

AUTH_SCOPE_DENIED = _define(
    "OG0101",
    "Token lacks the required scope (403).",
)

AUTH_CREDS_INCOMPLETE = _define(
    "OG0102",
    "Credentials are incomplete — configure every required field.",
)


# ============================================================================
# OG0200..OG0299 — Upstream API envelope / response shape
# ============================================================================

UPSTREAM_FAILURE = _define(
    "OG0200",
    "Upstream reported a failure.",
)

UPSTREAM_HTTP_ERROR = _define(
    "OG0201",
    "Upstream returned an HTTP error.",
)

UPSTREAM_NON_JSON = _define(
    "OG0202",
    "Upstream response was not valid JSON.",
)

UPSTREAM_UNEXPECTED = _define(
    "OG0299",
    "Upstream returned an unexpected response.",
)


# ============================================================================
# OG0300..OG0399 — Asset inventory
# ============================================================================

ASSET_CACHE_WRITE_FAILED = _define(
    "OG0300",
    "Failed to write the asset inventory cache file.",
)

ASSET_RANGE_INVALID = _define(
    "OG0301",
    "Invalid range — min_value must be less than or equal to max_value.",
)


# ============================================================================
# Public API — dict alias + structured error type + helpers
# ============================================================================

# Public read-only alias. Kept separately from _REGISTRY so callers who
# want to iterate don't accidentally mutate the authoritative map.
DEFAULT_MESSAGES: dict[str, str] = dict(_REGISTRY)


@dataclass(frozen=True)
class OGError:
    """Structured error the API layer returns to the frontend.

    The frontend looks up ``t('errors.' + code)`` for the localised
    message, falling back to ``message`` when the translation is
    missing. ``params`` carries interpolation data for the i18n
    template (e.g. ``{"host": "oufa.co"}`` for the DNS-failed case).
    """
    code: str
    message: str
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Wire representation. Callers merge this into their own
        response dicts — mergeable with ``{"ok": False, **err.to_dict()}``.
        """
        return {
            "error_code":   self.code,
            "error":        self.message,
            "error_params": self.params,
        }


def make_error(
    code: str,
    params: Optional[dict] = None,
    override_message: Optional[str] = None,
) -> OGError:
    """Build an OGError.

    ``override_message`` lets the caller pass in a specific English
    message (e.g. an upstream's ``details`` text) that overrides
    ``DEFAULT_MESSAGES[code]``. The translation lookup still uses
    ``code`` — the override is only a fallback for when no translation
    exists, and a diagnostic for logs.

    Unknown ``code`` softly maps to ``NETWORK_ERROR`` with the override
    carrying the real detail — better than crashing on a typo in a
    caller (which would surface as a 500).
    """
    if code not in _REGISTRY:
        code = NETWORK_ERROR
    message = override_message or _REGISTRY[code]
    return OGError(code=code, message=message, params=params or {})


def classify_exception(exc: Exception) -> OGError:
    """Map a caught exception to a structured OGError.

    Pattern-matches on common exception types + error substrings so
    most outbound-HTTP failures fall into a specific code rather than
    the generic NETWORK_ERROR bucket. Falls back to NETWORK_ERROR with
    the exception's own repr when nothing else fits.

    Kept import-light (httpx is optional — we do a best-effort check
    via type name) so callers in modules that don't use httpx can
    still benefit from the socket-level classifications.
    """
    text = str(exc)
    text_lower = text.lower()
    type_name = type(exc).__name__

    # socket.gaierror is the authoritative DNS-failure signal on every
    # platform — errno -2 on Linux, -3 on macOS, varying on Windows.
    # Match on the type, not the errno, so the check survives OS drift.
    if isinstance(exc, socket.gaierror):
        return make_error(
            DNS_RESOLVE_FAILED,
            params={"detail": text},
            override_message=f"DNS resolution failed: {text}",
        )

    # String-level fallbacks for exceptions that WRAP gaierror
    # (httpx.ConnectError does, for instance — the type is
    # ConnectError, but the underlying gaierror is chained via
    # __cause__). Matching on message text catches both cases.
    if ("name or service not known" in text_lower
            or "nodename nor servname provided" in text_lower
            or "name resolution" in text_lower
            or "temporary failure in name resolution" in text_lower
            or "getaddrinfo failed" in text_lower):
        return make_error(
            DNS_RESOLVE_FAILED,
            params={"detail": text},
            override_message=f"DNS resolution failed: {text}",
        )

    # httpx — matched by type name so we don't force the import.
    if type_name == "ConnectError":
        if "refused" in text_lower:
            return make_error(CONNECTION_REFUSED, override_message=text)
        return make_error(NETWORK_ERROR, override_message=text)
    if type_name in ("ConnectTimeout", "ReadTimeout", "WriteTimeout",
                     "PoolTimeout", "TimeoutException"):
        return make_error(CONNECTION_TIMEOUT, override_message=text)

    # TLS / SSL — covers ssl.SSLError, httpx.ProtocolError wrapping
    # an SSL layer, and certificate-verification failures.
    if ("certificate" in text_lower or "ssl:" in text_lower
            or "tls" in text_lower or "handshake" in text_lower):
        return make_error(TLS_HANDSHAKE_FAILED, override_message=text)

    # ConnectionRefusedError (stdlib) — rarely bubbles past httpx, but
    # some callers use raw sockets.
    if isinstance(exc, ConnectionRefusedError):
        return make_error(CONNECTION_REFUSED, override_message=text)

    return make_error(
        NETWORK_ERROR,
        override_message=f"{type_name}: {exc}",
    )
