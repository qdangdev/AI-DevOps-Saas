"""Repo endpoints — list GitHub repos, connect a repo, list connected repos."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from shared.core.security import decrypt
from shared.models.repo import Repo
from shared.schemas.repo import ConnectRepoRequest, GitHubRepoOut, RepoOut
from shared.services import github as gh

router = APIRouter(prefix="/repos", tags=["repos"])
log = structlog.get_logger(__name__)


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    # GitHub returns "2024-01-02T03:04:05Z"
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@router.get("/github", response_model=list[GitHubRepoOut])
async def list_github_repos(user: CurrentUser) -> list[GitHubRepoOut]:
    """List repos the user can access on GitHub (not necessarily connected to us yet)."""
    token = decrypt(user.github_access_token_enc)
    try:
        raw = await gh.list_repos(token)
    except gh.GitHubError as e:
        log.warning("repos.list_github_failed", user_id=str(user.id), error=str(e))
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "could not reach github") from e

    return [
        GitHubRepoOut(
            github_repo_id=r["id"],
            full_name=r["full_name"],
            default_branch=r.get("default_branch") or "main",
            private=r.get("private", False),
            html_url=r["html_url"],
            clone_url=r["clone_url"],
            description=r.get("description"),
            language=r.get("language"),
            pushed_at=_parse_iso(r.get("pushed_at")),
        )
        for r in raw
    ]


@router.get("", response_model=list[RepoOut])
async def list_connected_repos(user: CurrentUser, db: DbSession) -> list[Repo]:
    result = await db.scalars(
        select(Repo).where(Repo.owner_id == user.id).order_by(Repo.connected_at.desc())
    )
    return list(result)


@router.post("", response_model=RepoOut, status_code=status.HTTP_201_CREATED)
async def connect_repo(
    body: ConnectRepoRequest,
    user: CurrentUser,
    db: DbSession,
) -> Repo:
    """Connect a GitHub repo to this user's account.

    Idempotent on (owner_id, github_repo_id) — re-posting refreshes the cached metadata.
    """
    token = decrypt(user.github_access_token_enc)
    try:
        meta = await gh.get_repo(token, body.github_repo_id)
    except gh.GitHubError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e

    existing = await db.scalar(
        select(Repo).where(
            Repo.owner_id == user.id,
            Repo.github_repo_id == body.github_repo_id,
        )
    )
    if existing is not None:
        existing.full_name = meta["full_name"]
        existing.default_branch = meta.get("default_branch") or "main"
        existing.private = meta.get("private", False)
        existing.html_url = meta["html_url"]
        existing.clone_url = meta["clone_url"]
        log.info("repo.refreshed", repo_id=str(existing.id), full_name=existing.full_name)
        return existing

    repo = Repo(
        owner_id=user.id,
        github_repo_id=meta["id"],
        full_name=meta["full_name"],
        default_branch=meta.get("default_branch") or "main",
        private=meta.get("private", False),
        html_url=meta["html_url"],
        clone_url=meta["clone_url"],
    )
    db.add(repo)
    await db.flush()
    log.info("repo.connected", repo_id=str(repo.id), full_name=repo.full_name)
    return repo


@router.delete("/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_repo(repo_id: UUID, user: CurrentUser, db: DbSession) -> None:
    repo = await db.scalar(
        select(Repo).where(Repo.id == repo_id, Repo.owner_id == user.id)
    )
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repo not found")
    await db.delete(repo)
    log.info("repo.disconnected", repo_id=str(repo_id))
