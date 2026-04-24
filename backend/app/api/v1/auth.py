"""GitHub OAuth flow.

Flow:
  1. Frontend hits GET /auth/github/login → 302 to GitHub authorize URL.
     A signed state cookie protects against CSRF.
  2. GitHub redirects to GET /auth/github/callback?code=...&state=...
     We verify state, exchange code, upsert the user, mint a JWT, and 302 to the
     frontend with the access token in a fragment (#access_token=...).
  3. Frontend reads the fragment, stores the token in memory, scrubs the URL.
"""
from __future__ import annotations

import secrets
from typing import Annotated

import structlog
from fastapi import APIRouter, Cookie, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import DbSession
from app.core.config import get_settings
from app.core.security import create_token, encrypt
from app.models.user import User
from app.services import github as gh

router = APIRouter(prefix="/auth", tags=["auth"])
log = structlog.get_logger(__name__)
settings = get_settings()

STATE_COOKIE = "gh_oauth_state"
STATE_COOKIE_TTL = 600  # 10 min — enough for the GitHub round trip


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

    # Upsert user
    user = await db.scalar(select(User).where(User.github_id == gh_user.id))
    enc_token = encrypt(access_token)

    if user is None:
        user = User(
            github_id=gh_user.id,
            github_login=gh_user.login,
            email=gh_user.email,
            avatar_url=gh_user.avatar_url,
            github_access_token_enc=enc_token,
        )
        db.add(user)
        await db.flush()  # populate user.id before we mint the JWT
        log.info("user.created", user_id=str(user.id), github_login=gh_user.login)
    else:
        user.github_login = gh_user.login
        user.email = gh_user.email
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
    """Stateless logout — frontend just drops the token. Reserved here for future refresh-cookie clearing."""
    return Response(status_code=status.HTTP_204_NO_CONTENT)
