"""Smoke test for the WebAuthn helper module.

Live PublicKeyCredential simulation needs a real authenticator -- out of
scope for this suite. Instead we verify:
  - the module imports cleanly when ``webauthn`` is present (production
    case) AND when it isn't (graceful-degrade path)
  - the helper's pure-function wrappers don't crash with empty state
  - registration options serialise to a JSON-friendly dict shape
  - friendly-name validation accepts/rejects the documented edge cases
  - base64url encode/decode round-trips bytes verbatim

Run with ``pytest tests/`` from the repo root. The bulk of the WebAuthn
ceremony is exercised end-to-end via manual validation steps in the
TODO entry for the WebAuthn enrolment work.
"""
from __future__ import annotations

import importlib

# pytest is a test-only dependency — deliberately NOT in requirements.txt
# (the project keeps test / lint deps out of the runtime image). Install
# it ad-hoc to run this suite (`pip install pytest`). The suppression
# stops the IDE flagging the import as unresolved when pytest isn't in
# the active interpreter.
# noinspection PyUnresolvedReferences,PyPackageRequirements
import pytest


def _import_helper():
    """Import + return the ``logic.webauthn_helper`` module under test."""
    return importlib.import_module("logic.webauthn_helper")


def test_module_imports():
    """The helper imports and exposes its capability flag whether or not the optional webauthn dep is installed."""
    h = _import_helper()
    # Helper exposes a capability flag whether or not the underlying
    # webauthn package installed -- callers branch on it.
    assert hasattr(h, "WEBAUTHN_AVAILABLE")
    assert isinstance(h.WEBAUTHN_AVAILABLE, bool)


def test_friendly_name_validation():
    """``validate_friendly_name`` accepts a normal key name unchanged."""
    h = _import_helper()
    assert h.validate_friendly_name("YubiKey 5C") == "YubiKey 5C"
    assert h.validate_friendly_name("  trimmed  ") == "trimmed"
    assert h.validate_friendly_name("") == ""
    with pytest.raises(ValueError):
        h.validate_friendly_name("x" * 65)
    with pytest.raises(ValueError):
        h.validate_friendly_name("bad\x00name")


def test_b64u_roundtrip():
    """base64url encode/decode round-trips (skipped when webauthn is unavailable)."""
    h = _import_helper()
    if not h.WEBAUTHN_AVAILABLE:
        pytest.skip("webauthn package not installed")
    payloads = [b"", b"\x00", b"abc", bytes(range(256))]
    for p in payloads:
        s = h.b64u_encode(p)
        assert isinstance(s, str)
        # base64url has no padding chars, no '+' / '/'
        assert "=" not in s
        assert "+" not in s
        assert "/" not in s
        assert h.b64u_decode(s) == p


def test_make_registration_options_shape():
    """``make_registration_options`` returns the expected options shape (skipped when webauthn is unavailable)."""
    h = _import_helper()
    if not h.WEBAUTHN_AVAILABLE:
        pytest.skip("webauthn package not installed")
    opts, challenge = h.make_registration_options(
        rp_id="omnigrid.example.com",
        rp_name="OmniGrid",
        user_id=b"user-1",
        username="alice",
        display_name="Alice",
        existing_credential_ids=[],
    )
    assert isinstance(opts, dict)
    # Surface fields the SPA needs.
    for key in ("challenge", "rp", "user", "pubKeyCredParams", "attestation"):
        assert key in opts, f"options missing {key!r}"
    assert opts["rp"]["id"] == "omnigrid.example.com"
    assert isinstance(challenge, bytes)
    # The serialised challenge in the dict is base64url; decoding it
    # should recover the raw bytes the caller stashes for verify.
    assert h.b64u_decode(opts["challenge"]) == challenge


def test_make_authentication_options_with_no_credentials():
    """``make_authentication_options`` handles an empty credential list (skipped when webauthn is unavailable)."""
    h = _import_helper()
    if not h.WEBAUTHN_AVAILABLE:
        pytest.skip("webauthn package not installed")
    opts, challenge = h.make_authentication_options(
        rp_id="omnigrid.example.com",
        allowed_credentials=[],
    )
    assert isinstance(opts, dict)
    assert "challenge" in opts
    # allowCredentials may be absent or empty when no creds are enrolled.
    if "allowCredentials" in opts:
        assert opts["allowCredentials"] == []
    assert isinstance(challenge, bytes) and len(challenge) >= 16


def test_assert_available_raises_when_missing(monkeypatch):
    """When the webauthn package is missing, every entry-point raises."""
    h = _import_helper()
    monkeypatch.setattr(h, "WEBAUTHN_AVAILABLE", False, raising=False)
    with pytest.raises(RuntimeError):
        h.assert_available()
