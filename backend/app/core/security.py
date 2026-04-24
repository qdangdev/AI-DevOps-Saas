"""JWT minting/verification and symmetric encryption for stored secrets.

JWT model: short-lived access tokens (Authorization header), longer-lived refresh tokens
(httpOnly cookie). GitHub access tokens are encrypted with Fernet before persisting.
"""
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

settings = get_settings()
_fernet = Fernet(settings.encryption_key.encode())

JWT_ALGO = "HS256"
TokenType = Literal["access", "refresh"]


class TokenError(Exception):
    """Raised when a JWT cannot be decoded or has expired."""


def _now() -> datetime:
    return datetime.now(UTC)


def create_token(
    subject: str | int,
    token_type: TokenType,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Mint a JWT for the given subject."""
    if token_type == "access":
        ttl = timedelta(minutes=settings.jwt_access_ttl_minutes)
    else:
        ttl = timedelta(days=settings.jwt_refresh_ttl_days)

    now = _now()
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGO)


def decode_token(token: str, expected_type: TokenType | None = None) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError as e:
        raise TokenError("token expired") from e
    except jwt.InvalidTokenError as e:
        raise TokenError("invalid token") from e

    if expected_type and payload.get("type") != expected_type:
        raise TokenError(f"expected {expected_type} token, got {payload.get('type')}")
    return payload


def encrypt(plaintext: str) -> str:
    """Encrypt a secret (e.g., GitHub access token) for at-rest storage."""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise TokenError("could not decrypt secret") from e
