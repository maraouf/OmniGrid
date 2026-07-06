"""OIDC provider registry — the single source of truth for OmniGrid's
multi-provider SSO support.

OmniGrid supports N OpenID-Connect identity providers side by side. Each is an
immutable :class:`OidcProvider` descriptor in :data:`PROVIDERS`; everything
else — the auth-settings key space (:mod:`logic.auth`), the flow code
(:mod:`logic.oidc`), the ``/api/oidc/*`` routes, ``/api/auth/providers``, and
the settings API — reads its per-provider config through this registry, so
adding a third provider is one entry here plus its settings keys + i18n.

Settings-key scheme (:func:`setting_key`):

* The legacy **Authentik** provider has an EMPTY ``key_prefix`` so it keeps the
  original bare ``oidc_*`` setting keys and its existing ``/api/oidc/login``
  routes byte-for-byte — a deploy that already has Authentik configured is
  completely unaffected.
* Every other provider ``<id>`` namespaces its keys as ``oidc_<id>_*`` (e.g.
  ``oidc_unifiedsso_issuer_url``) and its routes as ``/api/oidc/<id>/login``.

Admin-role mapping is per-provider:

* ``admin_mode="group"`` — a user is admin iff a configured group name appears
  in the id_token ``groups`` claim (Authentik's model).
* ``admin_mode="role"`` — a user is admin iff the ``admin_role_claim`` claim
  equals ``admin_role_value`` (UnifiedSSO emits one ``role`` claim of
  ``"USER"`` / ``"ADMIN"``).

The ``id`` doubles as the ``users.auth_source`` value so each provider's users
are tracked separately and never conflated by a shared email.

This module is a dependency-free leaf (only the stdlib) so importing it can
never introduce a cycle with :mod:`logic.auth` / :mod:`logic.oidc`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class OidcProvider:
    """Immutable descriptor for one OIDC identity provider.

    ``id`` is the stable slug used as the route segment, the settings-key
    discriminator (via ``key_prefix``), and the ``users.auth_source`` value.
    """
    id: str
    label: str
    icon: str                       # icon slug under static/img/icons/
    key_prefix: str                 # "" for legacy authentik, "<id>_" otherwise
    admin_mode: str                 # "group" | "role"
    default_scopes: str
    supports_registration: bool = False   # RFC 7591 dynamic client registration
    admin_role_claim: str = "role"        # role-mode only
    admin_role_value: str = "ADMIN"       # role-mode only


# The registry. Order is the display order (login buttons, admin selector).
PROVIDERS: tuple[OidcProvider, ...] = (
    OidcProvider(
        id="authentik",
        label="Authentik",
        icon="authentik",
        key_prefix="",  # legacy bare oidc_* keys + /api/oidc/login routes
        admin_mode="group",
        default_scopes="openid email profile groups",
        supports_registration=False,
    ),
    OidcProvider(
        id="unifiedsso",
        label="UnifiedSSO",
        icon="unifiedsso",
        key_prefix="unifiedsso_",
        admin_mode="role",
        default_scopes="openid profile email roles",
        supports_registration=True,
        admin_role_claim="role",
        admin_role_value="ADMIN",
    ),
)

DEFAULT_PROVIDER_ID = "authentik"

_BY_ID: dict[str, OidcProvider] = {p.id: p for p in PROVIDERS}


def all_providers() -> tuple[OidcProvider, ...]:
    """Every registered provider, in display order."""
    return PROVIDERS


def get(provider_id: Optional[str]) -> Optional[OidcProvider]:
    """Look up a provider by id. A blank / missing id resolves to the default
    (legacy Authentik) so the bare ``/api/oidc/login`` route keeps working."""
    if not provider_id:
        return _BY_ID.get(DEFAULT_PROVIDER_ID)
    return _BY_ID.get(provider_id)


def require(provider_id: Optional[str]) -> OidcProvider:
    """Like :func:`get` but raises ``KeyError`` on an unknown id — callers that
    have already validated the id (route handlers) use this."""
    p = get(provider_id)
    if p is None:
        raise KeyError(f"unknown OIDC provider: {provider_id!r}")
    return p


def valid_auth_sources() -> frozenset[str]:
    """The set of ``users.auth_source`` values OmniGrid mints — ``"local"``
    plus every provider id. Enforced application-side by
    :func:`logic.auth.create_user` now that the DB CHECK no longer enumerates
    values (migration 008)."""
    return frozenset({"local", *(p.id for p in PROVIDERS)})


# ---------------------------------------------------------------------------
# Settings-key namespacing
# ---------------------------------------------------------------------------
def setting_key(provider: OidcProvider, name: str) -> str:
    """Settings-table key for ``name`` on ``provider``.

    Authentik (empty prefix) → ``oidc_<name>``; others → ``oidc_<id>_<name>``.
    """
    return f"oidc_{provider.key_prefix}{name}"


# Per-provider settings leaf-names. COMMON applies to every provider; the
# admin-mode-specific set is appended by :func:`provider_setting_names`.
COMMON_KEYS: tuple[str, ...] = (
    "enabled", "issuer_url", "client_id", "client_secret",
    "redirect_uri", "scopes", "verify_tls",
)
GROUP_MODE_KEYS: tuple[str, ...] = ("admin_group", "group_case_sensitive")
ROLE_MODE_KEYS: tuple[str, ...] = ("admin_role_claim", "admin_role_value")
# Leaf-names stored as booleans (coerced from their stringified form on read).
BOOL_KEYS: frozenset[str] = frozenset({"enabled", "verify_tls", "group_case_sensitive"})


def provider_setting_names(provider: OidcProvider) -> tuple[str, ...]:
    """The leaf-names that make up ``provider``'s settings block."""
    extra = GROUP_MODE_KEYS if provider.admin_mode == "group" else ROLE_MODE_KEYS
    return COMMON_KEYS + extra


def default_for(provider: OidcProvider, name: str) -> object:
    """Default value for the ``name`` leaf on ``provider`` (mirrors the legacy
    Authentik defaults byte-for-byte for the empty-prefix provider)."""
    if name == "enabled":
        return False
    if name in ("verify_tls", "group_case_sensitive"):
        return True
    if name == "scopes":
        return provider.default_scopes
    if name == "admin_group":
        return "omnigrid-admins"
    if name == "admin_role_claim":
        return provider.admin_role_claim
    if name == "admin_role_value":
        return provider.admin_role_value
    # issuer_url / client_id / client_secret / redirect_uri
    return ""
