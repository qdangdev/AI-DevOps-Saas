"""Auth-related request/response schemas."""
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class UserOut(BaseModel):
    id: UUID
    github_login: str
    email: EmailStr | None = None
    avatar_url: str | None = None

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="seconds until access token expires")
    user: UserOut


class GitHubLoginResponse(BaseModel):
    """Returned by GET /auth/github/login when called via XHR (instead of redirect)."""

    authorize_url: str
    state: str
