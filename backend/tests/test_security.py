"""Smoke tests for the JWT + Fernet + bcrypt helpers — first thing that breaks if env is wrong."""
import pytest

from shared.core.security import (
    TokenError,
    create_token,
    decode_token,
    decrypt,
    encrypt,
    hash_password,
    verify_password,
)


def test_jwt_roundtrip():
    token = create_token("user-123", "access")
    payload = decode_token(token, expected_type="access")
    assert payload["sub"] == "user-123"
    assert payload["type"] == "access"


def test_jwt_wrong_type_rejected():
    token = create_token("user-123", "refresh")
    with pytest.raises(TokenError):
        decode_token(token, expected_type="access")


def test_fernet_roundtrip():
    secret = "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    enc = encrypt(secret)
    assert enc != secret
    assert decrypt(enc) == secret


def test_password_roundtrip():
    pw = "correct horse battery staple"
    h = hash_password(pw)
    assert h != pw
    assert h.startswith("$2b$")
    assert verify_password(pw, h)


def test_password_wrong_rejected():
    h = hash_password("secret-1")
    assert not verify_password("secret-2", h)


def test_password_invalid_hash_returns_false():
    # Malformed hash must not crash the login path.
    assert not verify_password("anything", "not-a-bcrypt-hash")


def test_password_empty_rejected():
    with pytest.raises(ValueError):
        hash_password("")
