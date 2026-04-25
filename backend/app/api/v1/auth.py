"""Authentication routes — email/password + GitHub OAuth.

Two ways in, one User table, one JWT format. Both flows return a
`TokenResponse` with the same shape.

Email/password:
  POST /auth/signup  — create account, return JWT
  POST /auth/login   — exchange credentials for JWT

GitHub OAuth (existing):
  GET  /auth/github/login    — 302 to GitHub authorize
  GET  /auth/github/callback — GitHub returns here, we 302 to frontend with #access_token=...

Protected example:
  GET  /auth/me — returns the authenticated user, demonstrates `CurrentUser` dep

Logout is stateless — the frontend simply drops the token. We keep the route
so we have a place to clear refresh cookies once we add them.
"""
from __future__ import annotations

import secrets
from typing import Annotated

import structlog
from fastapi import APIRouter, Cookie, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession
from shared.core.config import get_settings
from shared.core.security import (
    create_token,
    encrypt,
    hash_password,
    verify_password,
)
from shared.models.user import User
from shared.schemas.auth import (
    LoginRequest,
    SignupRequest,
    TokenResponse,
    UserOut,
)
from shared.services import github as gh

router = APIRouter(prefix="/auth", tags=["auth"])
log = structlog.get_logger(__name__)
settings = get_settings()

STATE_COOKIE = "gh_oauth_state"
STATE_COOKIE_TTL = 600  # 10 min — enough for the GitHub round trip


def _token_response(user: User) -> TokenResponse:
    """Build the standard auth response payload — used by signup, login, and any future flow."""
    access = create_token(user.id, "access")
    return TokenResponse(
        access_token=access,
        expires_in=settings.jwt_access_ttl_minutes * 60,
        user=UserOut.model_validate(user),
    )


# --- email + password -------------------------------------------------------


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest, db: DbSession) -> TokenResponse:
    """Create an account and return a JWT.

    We hash the password with bcrypt before insert. If the email is already
    taken we return 409 — we deliberately *don't* reveal whether the existing
    account uses GitHub vs password auth, to avoid an account-enumeration
    oracle.
    """
    user = User(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered") from None

    log.info("user.signup", user_id=str(user.id), email=user.email)
    return _token_response(user)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: DbSession) -> TokenResponse:
    """Exchange email + password for a JWT.

    We always run `verify_password` (even for an unknown email, against a
    throwaway hash) so the response time doesn't leak whether the address
    exists. The error message is deliberately the same for "no such user"
    and "wrong password".
    """
    user = await db.scalar(select(User).where(User.email == body.email.lower()))

    valid = False
    if user and user.password_hash:
        valid = verify_password(body.password, user.password_hash)
    else:
        # Constant-time decoy: hash a dummy so unknown-email and wrong-password
        # take the same wall-clock. The result is ignored.
        verify_password(body.password, "$2b$12$" + "a" * 53)

    if not valid or user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")

    log.info("user.login", user_id=str(user.id), email=user.email)
    return _token_response(user)


# --- protected example ------------------------------------------------------


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    """Return the authenticated user.

    The `CurrentUser` annotation is the entire protection mechanism — FastAPI
    sees it, calls `get_current_user`, which validates the bearer JWT and
    loads the row. If the token is missing/expired/invalid the dep raises 401
    *before* this function runs.

    Any route that wants to be protected just adds `user: CurrentUser` to
    its signature.
    """
    return user


# --- GitHub OAuth -----------------------------------------------------------


@router.get("/github/login")
async def github_login() -> RedirectResponse:
    """Redirect the user to GitHub's authorize page."""
    state = secrets.token_urlsafe(32)
    authorize_url, _ = gh.build_authorize_url(state=state)

    response = RedirectResponse(authorize_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=STATE_COOKIE_TTL,
        httponly=True,
        secure=settings.env != "dev",
        samesite="lax",
        path=settings.api_v1_prefix + "/auth",
    )
    return response


@router.get("/github/callback")
async def github_callback(
    db: DbSession,
    code: Annotated[str, Query()],
    state: Annotated[str, Query()],
    cookie_state: Annotated[str | None, Cookie(alias=STATE_COOKIE)] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    if error:
        log.warning("github.oauth_user_denied", error=error)
        return RedirectResponse(
            f"{settings.frontend_base_url}/login?error={error}",
            status_code=status.HTTP_302_FOUND,
        )

    if not cookie_state or not secrets.compare_digest(cookie_state, state):
        log.warning("github.oauth_state_mismatch")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid state")

    try:
        access_token = await gh.exchange_code_for_token(code)
        gh_user = await gh.fetch_user(access_token)
    except gh.GitHubError as e:
        log.error("github.oauth_failed", error=str(e))
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "github auth failed") from e

    enc_token = encrypt(access_token)
    # GitHub may return no public email — fall back to a synthetic but unique
    # value so the NOT-NULL/unique constraint holds. The user can update it
    # later from settings.
    email = (gh_user.email or f"{gh_user.login}@github.placeholder").lower()

    # Try to match by github_id first (returning user). If they originally
    # signed up with email/password and are now linking GitHub, match by email.
    user = await db.scalar(select(User).where(User.github_id == gh_user.id))
    if user is None:
        user = await db.scalar(select(User).where(User.email == email))

    if user is None:
        user = User(
            email=email,
            github_id=gh_user.id,
            github_login=gh_user.login,
            avatar_url=gh_user.avatar_url,
            github_access_token_enc=enc_token,
        )
        db.add(user)
        await db.flush()  # populate user.id before we mint the JWT
        log.info("user.created", user_id=str(user.id), github_login=gh_user.login)
    else:
        user.github_id = gh_user.id
        user.github_login = gh_user.login
        user.avatar_url = gh_user.avatar_url
        user.github_access_token_enc = enc_token
        log.info("user.refreshed", user_id=str(user.id), github_login=gh_user.login)

    jwt_token = create_token(user.id, "access")

    # Clear state cookie, redirect to frontend with token in fragment.
    # Fragment (not query) so the token never hits server logs / referrer headers.
    response = RedirectResponse(
        f"{settings.frontend_base_url}/auth/callback#access_token={jwt_token}",
        status_code=status.HTTP_302_FOUND,
    )
    response.delete_cookie(STATE_COOKIE, path=settings.api_v1_prefix + "/auth")
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> Response:
    """Stateless logout — frontend drops the token. Reserved for future refresh-cookie clearing."""
    return Response(status_code=status.HTTP_204_NO_CONTENT)
