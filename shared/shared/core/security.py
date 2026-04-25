"""JWT minting/verification + Fernet encryption + bcrypt password hashing.

Three concerns live here on purpose — they're all "secret-shaped string in,
secret-shaped string out", and grouping them keeps a single place to audit
crypto choices.
"""
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from shared.core.config import get_settings

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
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise TokenError("could not decrypt secret") from e


# --- password hashing -------------------------------------------------------
# bcrypt with a per-hash salt. The output already encodes algorithm + cost +
# salt, so we just store the single string in users.password_hash. Cost 12 is
# the FastAPI/Django default for 2024 hardware — adjust here if our server
# fleet changes shape and re-hash on next successful login.
_BCRYPT_ROUNDS = 12


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("password must be non-empty")
    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(rounds=_BCRYPT_ROUNDS),
    ).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time check. Returns False on any malformed hash rather than raising."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False
