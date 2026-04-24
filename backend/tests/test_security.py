"""Smoke tests for the JWT + Fernet helpers — first thing that breaks if env is wrong."""
import pytest

from app.core.security import TokenError, create_token, decode_token, decrypt, encrypt


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
