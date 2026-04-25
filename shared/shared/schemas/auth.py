"""Auth-related request/response schemas."""
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class UserOut(BaseModel):
    """Public user shape — never includes password_hash or GitHub token."""

    id: UUID
    email: EmailStr
    github_login: str | None = None
    avatar_url: str | None = None

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="seconds until access token expires")
    user: UserOut


class GitHubLoginResponse(BaseModel):
    authorize_url: str
    state: str


# --- email + password flow --------------------------------------------------


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
