"""TOTP (RFC 6238) two-factor authentication for local-auth users.

End-to-end:
  - Secret generation (pyotp.random_base32 -> 32-char base32)
  - Encryption at rest using a SESSION_SECRET-derived Fernet key
  - TOTP code verification with +/-1 step tolerance (30s window)
  - Backup code generation (10 codes, 8 digits each, formatted "XXXX YYYY")
  - Backup code encrypted-at-rest (recoverable for one-time display in
    Profile) + single-use enforcement via per-entry ``used_at`` flags
  - Lockout state per user (failed counter + locked_until epoch)
  - Per-user audit lines via the standard print('[totp] ...') tag

Authentik users are EXCLUDED -- their auth_source='authentik' short-
circuits enrollment + login flow at the call sites in main.py.

Encryption rationale: TOTP secrets are sensitive (anyone with the
secret can generate valid codes for the user). Encrypting at rest with
a SESSION_SECRET-derived key means a leaked DB without the matching
.env can't be replayed. Operators must rotate SESSION_SECRET AFTER
enabling 2FA -- anyone who held an earlier .env copy could decrypt.

Backup codes are stored ENCRYPTED rather than hashed so the Profile
page can re-display them with a hide/unhide eye (operator UX choice).
Verification compares decrypted codes; used codes flip ``used_at``
rather than dropping the row so the display retains the strikethrough
list. Single-use is enforced strictly: a code with ``used_at`` set is
rejected even if the value matches.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
import time
from typing import Optional

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# ----------------------------------------------------------------------------
# Fernet key derivation -- keyed off SESSION_SECRET so rotating that one env
# var rotates EVERY at-rest TOTP secret + backup code at once. Cached at
# module level so repeated calls don't re-run HKDF.
# ----------------------------------------------------------------------------
_FERNET_CACHE: dict[bytes, Fernet] = {}
_HKDF_INFO = b"omnigrid-totp-fernet-v1"


def _derive_fernet_key(session_secret: bytes) -> bytes:
    """HKDF-SHA256 over the session secret -> 32 raw bytes -> urlsafe b64.

    Fernet expects a 32-byte url-safe base64-encoded key. We don't store
    the derived key anywhere; it lives only in the in-process cache.
    """
    if not session_secret:
        raise RuntimeError("SESSION_SECRET is empty -- cannot derive TOTP key")
    raw = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"omnigrid-totp-salt-v1",
        info=_HKDF_INFO,
    ).derive(session_secret)
    return base64.urlsafe_b64encode(raw)


def _fernet() -> Fernet:
    # Late import keeps the auth module from depending on totp at import.
    from logic import auth as _auth
    secret = _auth.SESSION_SECRET
    if secret in _FERNET_CACHE:
        return _FERNET_CACHE[secret]
    key = _derive_fernet_key(secret)
    f = Fernet(key)
    _FERNET_CACHE[secret] = f
    return f


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a string for at-rest storage. Caller wraps exceptions."""
    if not plaintext:
        raise ValueError("plaintext is empty")
    return _fernet().encrypt(plaintext.encode()).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """Reverse encrypt_secret. Raises InvalidToken on bad input."""
    if not ciphertext:
        raise InvalidToken("ciphertext is empty")
    return _fernet().decrypt(ciphertext.encode("ascii")).decode()


# ----------------------------------------------------------------------------
# TOTP secret + verification
# ----------------------------------------------------------------------------
def generate_secret() -> str:
    """Return a fresh 32-char base32 TOTP secret."""
    return pyotp.random_base32()


def verify_code(secret_plain: str, code: str) -> bool:
    """Verify a 6-digit TOTP against a plaintext secret.

    +/-1 step tolerance (30-second windows on each side) covers minor
    clock skew between the user's authenticator app and the server.
    Strips spaces / dashes the user might paste from a QR-scan helper.
    """
    if not secret_plain or not code:
        return False
    cleaned = (code or "").replace(" ", "").replace("-", "").strip()
    if not cleaned.isdigit() or len(cleaned) != 6:
        return False
    try:
        return pyotp.TOTP(secret_plain).verify(cleaned, valid_window=1)
    except (ValueError, TypeError, binascii.Error):
        return False


def provisioning_uri(secret_plain: str, username: str, issuer: str = "OmniGrid") -> str:
    """Standard ``otpauth://`` URI for a QR code."""
    return pyotp.TOTP(secret_plain).provisioning_uri(
        name=username, issuer_name=issuer,
    )


# ----------------------------------------------------------------------------
# Backup codes
# ----------------------------------------------------------------------------
def _format_code(digits: str) -> str:
    """Format 8 raw digits as ``XXXX YYYY`` for display."""
    if len(digits) != 8 or not digits.isdigit():
        raise ValueError("backup code must be 8 digits")
    return f"{digits[:4]} {digits[4:]}"


def format_backup_code_for_display(plain: str) -> str:
    """Idempotent: accepts ``XXXX YYYY`` or ``XXXXYYYY``."""
    cleaned = (plain or "").replace(" ", "").replace("-", "").strip()
    return _format_code(cleaned)


def _normalise_for_compare(code: str) -> str:
    return (code or "").replace(" ", "").replace("-", "").strip()


def generate_backup_codes(n: int = 10) -> list[str]:
    """Return ``n`` newly-generated backup codes formatted ``XXXX YYYY``."""
    codes: list[str] = []
    for _ in range(n):
        digits = "".join(secrets.choice("0123456789") for _ in range(8))
        codes.append(_format_code(digits))
    return codes


def _backup_code_hash(plain: str) -> str:
    """SHA-256 hex of the canonical 8-digit form. — used
    for O(1) consume-time lookup so we no longer decrypt every code on
    every attempt. Hash-only access is safe: the hash is the proof the
    user knew the plaintext, and the encrypted blob still backs the
    Profile-side reveal flow."""
    canonical = _normalise_for_compare(plain)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def encrypt_backup_codes(plain_codes: list[str]) -> str:
    """Encrypt + JSON-pack a fresh list of backup codes for storage.

    Each entry has shape
    ``{"code_encrypted": "<fernet>", "code_hash": "<sha256>", "used_at": null}``.
    The ``code_hash`` field (added) lets ``consume_backup_code``
    look up by hash in O(1) instead of decrypting every entry on every
    attempt; the encrypted blob is still the source for the Profile-side
    reveal flow.
    """
    out = []
    for c in plain_codes:
        normalised = format_backup_code_for_display(c)
        canonical = normalised.replace(" ", "")
        out.append({
            "code_encrypted": encrypt_secret(canonical),
            "code_hash": _backup_code_hash(canonical),
            "used_at": None,
        })
    return json.dumps(out)


def decrypt_backup_codes(stored_json: Optional[str]) -> list[dict]:
    """Reverse ``encrypt_backup_codes``. Returns ``[{code, used_at}, ...]``.

    ``code`` is the displayable ``XXXX YYYY`` form. Empty / malformed
    input returns ``[]`` so callers don't need a try/except.
    """
    if not stored_json:
        return []
    try:
        raw = json.loads(stored_json)
    except (ValueError, TypeError):
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        ct = entry.get("code_encrypted")
        if not isinstance(ct, str) or not ct:
            continue
        try:
            plain = decrypt_secret(ct)
        except InvalidToken:
            continue
        out.append({
            "code": format_backup_code_for_display(plain),
            "used_at": entry.get("used_at"),
        })
    return out


def consume_backup_code(stored_json: Optional[str], attempt: str) -> tuple[bool, Optional[str]]:
    """Return ``(matched, new_stored_json)``.

    On match, the entry's ``used_at`` is set to the current epoch second
    and a new JSON blob is returned. On no-match (or all entries already
    used), returns ``(False, None)``.

    preferred path is O(1) lookup by ``code_hash``
    (SHA-256 of the canonical 8-digit form). Pre-fix entries lack
    the hash field — for those we fall back to the legacy decrypt-
    and-compare loop. Backfill happens lazily: a successful legacy-
    path match also writes the missing ``code_hash`` for the OTHER
    not-yet-hashed entries before returning, so the second consume
    on the same record is fast even without a schema migration.
    """
    if not stored_json:
        return False, None
    try:
        raw = json.loads(stored_json)
    except (ValueError, TypeError):
        return False, None
    if not isinstance(raw, list):
        return False, None
    target = _normalise_for_compare(attempt)
    if len(target) != 8 or not target.isdigit():
        return False, None
    target_hash = hashlib.sha256(target.encode("ascii")).hexdigest()

    # Fast path — O(1) hash equality. Skips decrypt entirely.
    legacy_entries: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        if entry.get("used_at"):
            continue
        h = entry.get("code_hash")
        if h:
            if h == target_hash:
                entry["used_at"] = int(time.time())
                return True, json.dumps(raw)
            continue  # hash present but no match — skip without decrypt
        legacy_entries.append(entry)

    # Legacy path — decrypt-and-compare for entries that pre-date.
    # Backfill code_hash on every entry we touch so the next consume
    # takes the fast path.
    matched = False
    for entry in legacy_entries:
        ct = entry.get("code_encrypted")
        if not isinstance(ct, str) or not ct:
            continue
        try:
            plain = decrypt_secret(ct)
        except InvalidToken:
            continue
        canonical = _normalise_for_compare(plain)
        # Backfill the hash regardless of match so subsequent attempts
        # of OTHER codes go through the fast path too.
        entry["code_hash"] = hashlib.sha256(canonical.encode("ascii")).hexdigest()
        if not matched and canonical == target:
            entry["used_at"] = int(time.time())
            matched = True
    if matched:
        return True, json.dumps(raw)
    # Persist any backfilled hashes even on no-match so the legacy
    # decrypt loop runs only once per code lifetime.
    if any("code_hash" in e for e in legacy_entries):
        return False, json.dumps(raw)
    return False, None
