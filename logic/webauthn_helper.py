"""WebAuthn / FIDO2 passkey support for local-auth users (#381).

Companion to ``logic/totp.py`` -- second factor that doesn't require the
user to type a 6-digit code. Users enrol passkeys (1Password / iCloud
Keychain / hardware keys / platform authenticators) from Profile and
pick "Use a passkey" at the second-factor login step.

Design choices (single-tenant homelab):
  - Attestation: ``none`` -- we don't validate device origin.
  - User-verification: ``preferred`` -- nudge platform authenticators
    into requiring biometric / PIN, but tolerate keys that don't.
  - Resident-key (discoverable credential): ``preferred`` so 1Password
    / iCloud Keychain offer usernameless autofill.
  - Both transport types accepted -- ``platform`` (Touch ID / Windows
    Hello / Android fingerprint) AND ``cross-platform`` (USB / NFC /
    BLE keys, plus desktop password managers that ship as cross-
    platform authenticators).
  - RP ID = the host the SPA is served from. The caller derives this
    from ``request.url.hostname`` on every challenge so dev (localhost)
    and prod (the operator's NPM-fronted domain) both work without
    settings.
  - Origin verification on every assertion = the full request origin
    of the login request. Rejects assertions presented at a different
    host.
  - Sign-counter monotonicity -- if the authenticator's reported
    signCount goes backwards or stays the same, treat as a clone and
    reject. ``0`` is a valid value (lots of authenticators -- including
    most password managers -- never increment), so 0 -> 0 is allowed
    only when we've never seen a non-zero value for that credential.

Encoding: blobs cross the wire as ``base64url`` strings (no padding).
The ``webauthn`` package ships ``bytes_to_base64url`` / ``base64url_to_bytes``
helpers we re-export so the SPA's ``b64uEncode`` / ``b64uDecode`` map
1:1 across the boundary.

The challenge store lives in ``main.py`` (alongside ``_totp_challenges``)
because the same dispatcher decides "you need a 2nd factor; choose TOTP
or passkey" -- keeping both in one process-local dict keeps the state
machine simple. This module is pure functions over the ``webauthn``
package; it doesn't reach into FastAPI or the DB.

Authentik users skip every passkey path -- auth_source='local' is the
gate at the call sites in main.py. Bearer-token API requests bypass too
(no cookies -> no challenge state -> no passkey UX).
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

# Lazy-imported so the module can be imported on a fresh deploy where
# the operator hasn't yet ``pip install``'d the new dependency. Every
# entrypoint validates ``WEBAUTHN_AVAILABLE`` first.
try:
    from webauthn import (
        generate_authentication_options,
        generate_registration_options,
        options_to_json,
        verify_authentication_response,
        verify_registration_response,
    )
    from webauthn.helpers import (
        base64url_to_bytes,
        bytes_to_base64url,
    )
    from webauthn.helpers.cose import COSEAlgorithmIdentifier
    from webauthn.helpers.structs import (
        AttestationConveyancePreference,
        AuthenticatorSelectionCriteria,
        AuthenticatorTransport,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    WEBAUTHN_AVAILABLE = True
    WEBAUTHN_IMPORT_ERROR: Optional[str] = None
except Exception as _import_err:  # pragma: no cover - import-time guard
    WEBAUTHN_AVAILABLE = False
    WEBAUTHN_IMPORT_ERROR = str(_import_err)
    base64url_to_bytes = None  # type: ignore[assignment]
    bytes_to_base64url = None  # type: ignore[assignment]


# COSE algorithm preferences -- ES256 first (every passkey supports it),
# RS256 second (some Windows Hello keys), EdDSA third (newer hardware).
_COSE_ALGS = (
    -7,    # ES256
    -257,  # RS256
    -8,    # EdDSA
)


# Friendly-name hard caps. 1..64 chars, no control bytes. Operator can
# rename later via PATCH; callers enforce these on every register-
# finish + (future) rename endpoint.
_FRIENDLY_NAME_MAX = 64
_FRIENDLY_NAME_RE = re.compile(r"^[\x20-\x7E -￿]{1,64}$")


def assert_available() -> None:
    """Raise RuntimeError if the ``webauthn`` package isn't installed.

    Call sites in main.py wrap in HTTPException(503, ...) so the SPA
    can render a clean "passkeys aren't available on this server" hint.
    """
    if not WEBAUTHN_AVAILABLE:
        raise RuntimeError(
            "WebAuthn library not installed. Add `webauthn>=2.0` to "
            "requirements.txt and reinstall. Original import error: "
            f"{WEBAUTHN_IMPORT_ERROR}"
        )


def b64u_encode(data: bytes) -> str:
    assert_available()
    return bytes_to_base64url(data)


def b64u_decode(s: str) -> bytes:
    assert_available()
    return base64url_to_bytes(s)


def validate_friendly_name(name: str) -> str:
    """Normalise + validate a credential friendly name.

    Returns the trimmed name or raises ValueError. Empty input is
    allowed at the API level (the route assigns "Passkey" as the
    default), so callers should fall back rather than reject empty
    strings here.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return ""
    if len(cleaned) > _FRIENDLY_NAME_MAX:
        raise ValueError(
            f"Friendly name too long (max {_FRIENDLY_NAME_MAX} chars)."
        )
    if not _FRIENDLY_NAME_RE.match(cleaned):
        raise ValueError("Friendly name contains invalid characters.")
    return cleaned


# ----------------------------------------------------------------------------
# Registration flow
# ----------------------------------------------------------------------------
def make_registration_options(
    *,
    rp_id: str,
    rp_name: str,
    user_id: bytes,
    username: str,
    display_name: str,
    existing_credential_ids: Iterable[bytes],
) -> tuple[dict, bytes]:
    """Generate ``PublicKeyCredentialCreationOptions`` for the SPA.

    Returns ``(options_dict, raw_challenge)``. The caller stashes the
    raw challenge in the in-memory store keyed by user_id; the dict
    is JSON-serialised to the browser so ``navigator.credentials
    .create()`` can consume it.

    ``existing_credential_ids`` populates ``excludeCredentials`` so the
    authenticator refuses to re-enrol an already-registered key. The
    wire shape passes the bytes; ``options_to_json`` handles the
    base64url encoding.
    """
    assert_available()
    excluded = [
        PublicKeyCredentialDescriptor(id=cid)
        for cid in existing_credential_ids
        if cid
    ]
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_id=user_id,
        user_name=username,
        user_display_name=display_name or username,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        supported_pub_key_algs=[
            COSEAlgorithmIdentifier(a) for a in _COSE_ALGS
        ],
        exclude_credentials=excluded,
    )
    # ``options_to_json`` returns a JSON-encoded string; we re-parse so
    # the SPA gets an object literal it can unwrap field-by-field.
    import json as _json
    options_dict = _json.loads(options_to_json(options))
    # WebAuthn Level 3 `hints` field — nudges the browser toward the
    # cross-platform picker so password managers (1Password / Bitwarden
    # / iCloud Keychain) get offered alongside platform authenticators
    # (Touch ID / Windows Hello). Without this, some browsers funnel
    # straight to the OS-native picker and never give the password-
    # manager extension a chance to surface its "save passkey" sheet
    # (#431). The webauthn lib doesn't expose this in its dataclass yet,
    # so we splice it onto the dict after serialisation.
    options_dict["hints"] = ["client-device", "hybrid", "security-key"]
    return options_dict, bytes(options.challenge)


def verify_registration(
    *,
    credential_json: dict,
    expected_challenge: bytes,
    expected_origin: str,
    expected_rp_id: str,
) -> dict:
    """Verify a ``navigator.credentials.create()`` response.

    Returns ``{credential_id: bytes, public_key: bytes, sign_count: int,
    transports: list[str]}`` on success. Raises on every failure path
    -- the caller wraps in HTTPException(400, ...).

    ``credential_json`` is the dict the SPA POSTs; structure matches
    the WebAuthn ``PublicKeyCredential`` interface with the ArrayBuffer
    fields already encoded as base64url strings.
    """
    assert_available()
    verification = verify_registration_response(
        credential=credential_json,
        expected_challenge=expected_challenge,
        expected_origin=expected_origin,
        expected_rp_id=expected_rp_id,
        require_user_verification=False,
    )
    transports_raw = (credential_json.get("response") or {}).get("transports") or []
    # Whitelist against the documented `AuthenticatorTransport` enum
    # values (#463 / BUG-008). Pre-fix any client-supplied string was
    # persisted verbatim — a quirky / malicious client could store
    # `transports=["evil"]` which then made `make_authentication_options`
    # fall back to dropping the field entirely. Filter to the official
    # set so the row stays honest. Lower-case before the membership
    # check; the upstream enum values are all lower-case.
    _ALLOWED_TRANSPORTS = {"usb", "nfc", "ble", "internal", "hybrid"}
    transports = [
        t.lower() for t in transports_raw
        if isinstance(t, str) and t.lower() in _ALLOWED_TRANSPORTS
    ]
    # #601 — when the browser doesn't emit `response.transports` (older
    # Chrome / 1Password / various enterprise WebAuthn implementations),
    # fall back to inferring from `authenticatorAttachment`. The top-
    # level field on the PublicKeyCredential reports 'platform' for
    # built-in authenticators (Touch ID / Windows Hello / Android
    # biometric) and 'cross-platform' for roaming auths (USB key /
    # 1Password browser extension / phone-as-authenticator). Map to
    # the matching transports so the credential row carries enough
    # data for the assertion-options builder to surface the correct
    # picker on the next login. Without this, empty-transport rows
    # fall back to the default-everything hint added in
    # `make_authentication_options`, which works but loses the
    # operator's intent ("I enrolled this AS Touch ID specifically").
    if not transports:
        attachment = (credential_json.get("authenticatorAttachment") or "").strip().lower()
        if attachment == "platform":
            transports = ["internal"]
        elif attachment == "cross-platform":
            transports = ["hybrid", "usb"]
    return {
        "credential_id": bytes(verification.credential_id),
        "public_key": bytes(verification.credential_public_key),
        "sign_count": int(verification.sign_count),
        "transports": transports,
    }


# ----------------------------------------------------------------------------
# Authentication (login second-factor) flow
# ----------------------------------------------------------------------------
def make_authentication_options(
    *,
    rp_id: str,
    allowed_credentials: Iterable[dict],
) -> tuple[dict, bytes]:
    """Generate ``PublicKeyCredentialRequestOptions`` for the login step.

    ``allowed_credentials`` is an iterable of ``{credential_id: bytes,
    transports: list[str]}`` dicts -- the credentials registered for
    this user. The ``allowCredentials`` list scopes the assertion to
    this user's keys (browser refuses anything else).

    Returns ``(options_dict, raw_challenge)``.
    """
    assert_available()
    allow = []
    for c in allowed_credentials:
        cid = c.get("credential_id")
        if not cid:
            continue
        ts = c.get("transports") or []
        # #601 / #602 — Safari/Chrome on macOS default to the hybrid
        # (QR) flow at assertion time when a credential's stored
        # transports list is missing `internal` (common when the
        # browser emitted `["hybrid"]` only at registration, OR when
        # the field came back empty entirely). Listing transports
        # without `internal` tells the browser "this credential is
        # NOT on the local device" — it then dives straight to QR
        # without offering Touch ID / iCloud Keychain / a password-
        # manager extension, even when those would actually work.
        # Force-union `['internal', 'hybrid']` into whatever's stored
        # so the browser ALWAYS considers the local platform AND
        # cross-device sign-in. If a credential was registered as a
        # USB security key, `['usb', 'internal', 'hybrid']` tells the
        # browser "consider all three" — the OS-level authenticator
        # query still rejects the wrong device, but the picker
        # surfaces every plausible option. The previous narrower
        # behaviour (default-only-when-empty) didn't cover the
        # `["hybrid"]`-only stored case.
        ts_set = {t for t in ts if isinstance(t, str)}
        ts_set.update({"internal", "hybrid"})
        # #602 — order matters: WebAuthn defines `transports` as an
        # ORDERED sequence of preferred transports, and several
        # browsers (notably Safari on macOS) use the FIRST listed
        # transport as the default-UI hint. Sorted alphabetically the
        # list reads `['hybrid', 'internal']` — `hybrid` first nudges
        # Safari toward the QR flow even when `internal` is also
        # listed. Force `internal` first when present, then USB / BLE /
        # NFC / hybrid in their natural fallback order so the picker
        # tries the local platform before any cross-device flow.
        _TRANSPORT_ORDER = ["internal", "usb", "ble", "nfc", "hybrid"]
        ts = [t for t in _TRANSPORT_ORDER if t in ts_set]
        try:
            transport_enums = [
                AuthenticatorTransport(str(t).lower())
                for t in ts
                if t
            ] or None
        except ValueError:
            # Unknown transport string from a future authenticator --
            # drop the field rather than raise. The browser still
            # accepts the assertion when transports is omitted.
            transport_enums = None
        allow.append(
            PublicKeyCredentialDescriptor(
                id=cid,
                transports=transport_enums,
            )
        )
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=allow,
        # #604 — escalated from PREFERRED to REQUIRED. With PREFERRED,
        # Chrome on macOS could satisfy the assertion via the hybrid
        # (QR) flow without a biometric — and with QR available as
        # an option, Chrome's picker DEFAULTED to it for our two-
        # credential case. REQUIRED forces a UV-capable
        # authenticator which is exactly what Touch ID / iCloud
        # Keychain / 1Password's browser extension provide; the QR
        # flow on a paired phone also satisfies REQUIRED via the
        # phone's biometric, so cross-device sign-in still works.
        # Net effect: the picker offers Touch ID / 1Password BEFORE
        # QR because they're the local UV-capable authenticators.
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    import json as _json
    options_dict = _json.loads(options_to_json(options))
    # WebAuthn Level 3 `hints` field on the assertion (#600) — same
    # nudge the registration path uses (line ~203). Without this,
    # Safari and Chrome on macOS frequently default to the hybrid
    # (QR-code) flow when stored credentials don't carry explicit
    # `internal` transports, even when Touch ID + iCloud Keychain
    # AND a password-manager extension (1Password / Bitwarden) BOTH
    # have valid passkeys. Listing all three hints in priority
    # order tells the browser to show the platform picker first
    # (Touch ID / Windows Hello / Android biometric), then
    # hybrid for cross-device sign-in, then security keys.
    # Splice on after serialisation because the webauthn library's
    # dataclass doesn't expose the field yet.
    options_dict["hints"] = ["client-device", "hybrid", "security-key"]
    return options_dict, bytes(options.challenge)


def verify_authentication(
    *,
    credential_json: dict,
    expected_challenge: bytes,
    expected_origin: str,
    expected_rp_id: str,
    public_key: bytes,
    current_sign_count: int,
) -> dict:
    """Verify a ``navigator.credentials.get()`` response.

    Returns ``{new_sign_count: int}`` on success. Raises on signature
    mismatch / origin mismatch / clone detection. Sign-counter check is
    monotonic-strict EXCEPT for authenticators that report 0 (passkey
    managers usually do) -- ``0 == current`` is allowed only while
    ``current`` is also 0.
    """
    assert_available()
    verification = verify_authentication_response(
        credential=credential_json,
        expected_challenge=expected_challenge,
        expected_rp_id=expected_rp_id,
        expected_origin=expected_origin,
        credential_public_key=public_key,
        credential_current_sign_count=current_sign_count,
        require_user_verification=False,
    )
    new_sc = int(verification.new_sign_count)
    # `verify_authentication_response` already rejects sign-count
    # regressions for authenticators that DO report a counter. The
    # 0/0 case is left to us: that's normal for password managers.
    # Defence-in-depth (#462 / BUG-007): explicitly reject any
    # backwards step in the non-zero range. Past versions of Duo's
    # webauthn lib (1.x) silently allowed regressions in some edge
    # cases; pinning >=2.0 covers the published behaviour but keeping
    # this guard means an upstream regression doesn't silently let
    # cloned authenticators through.
    if new_sc and current_sign_count and new_sc < current_sign_count:
        raise ValueError(
            f"Sign counter regression: new={new_sc} < current={current_sign_count}"
        )
    return {"new_sign_count": new_sc}
