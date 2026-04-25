"""Repo-related schemas."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class GitHubRepoOut(BaseModel):
    """A repo as it appears in the user's GitHub account (not yet connected)."""

    github_repo_id: int
    full_name: str
    default_branch: str
    private: bool
    html_url: str
    clone_url: str
    description: str | None = None
    language: str | None = None
    pushed_at: datetime | None = None


class ConnectRepoRequest(BaseModel):
    github_repo_id: int = Field(..., description="numeric GitHub repo id")


class RepoOut(BaseModel):
    id: UUID
    github_repo_id: int
    full_name: str
    default_branch: str
    private: bool
    html_url: str
    connected_at: datetime

    model_config = {"from_attributes": True}
