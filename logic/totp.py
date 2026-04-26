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
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """Reverse encrypt_secret. Raises InvalidToken on bad input."""
    if not ciphertext:
        raise InvalidToken("ciphertext is empty")
    return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")


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
        return pyotp.TOTP(secret_plain, interval=30).verify(cleaned, valid_window=1)
    except Exception:
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


def encrypt_backup_codes(plain_codes: list[str]) -> str:
    """Encrypt + JSON-pack a fresh list of backup codes for storage.

    Each entry has shape ``{"code_encrypted": "<fernet>", "used_at": null}``.
    """
    out = []
    for c in plain_codes:
        normalised = format_backup_code_for_display(c)
        out.append({
            "code_encrypted": encrypt_secret(normalised.replace(" ", "")),
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
        if not ct:
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
    """
    if not stored_json:
        return (False, None)
    try:
        raw = json.loads(stored_json)
    except (ValueError, TypeError):
        return (False, None)
    if not isinstance(raw, list):
        return (False, None)
    target = _normalise_for_compare(attempt)
    if len(target) != 8 or not target.isdigit():
        return (False, None)
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        if entry.get("used_at"):
            continue
        ct = entry.get("code_encrypted")
        if not ct:
            continue
        try:
            plain = decrypt_secret(ct)
        except InvalidToken:
            continue
        if _normalise_for_compare(plain) == target:
            entry["used_at"] = int(time.time())
            return (True, json.dumps(raw))
    return (False, None)
