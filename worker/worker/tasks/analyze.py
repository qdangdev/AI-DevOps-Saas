"""analyzer queue — fetch repo metadata, ask Claude to characterize it, publish events.

Celery tasks are sync, but the analyzer + DB are async. We use `asyncio.run`
to bridge — the task itself is short-lived and doesn't share state with
anything else in the worker process, so a fresh event loop per task is fine.

Args (positional, matching backend.app.jobs.enqueue.enqueue_analyze):
  deployment_id — for progress events on the deployment:{id}:events stream
  repo_id       — UUID of the repos row to analyze
"""
from __future__ import annotations

import asyncio
from uuid import UUID

import structlog
from celery import shared_task
from sqlalchemy import select

from shared.analysis import analyze_repo
from shared.core.database import db_session
from shared.core.security import decrypt
from shared.models.repo import Repo
from shared.models.user import User
from shared.services.github import close_client as close_github_client
from worker.progress import publish

log = structlog.get_logger(__name__)


@shared_task(
    name="worker.tasks.analyze.run",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def run(self, deployment_id: str, repo_id: str) -> dict:
    """Analyze a repo. Returns a JSON-safe dict the API can persist alongside
    the deployment row."""
    log.info("analyze.start", repo_id=repo_id, deployment_id=deployment_id, task_id=self.request.id)
    publish(deployment_id, "analyze.started")
    try:
        result = asyncio.run(_do_analyze(UUID(deployment_id), UUID(repo_id)))
    except Exception as e:
        publish(deployment_id, "analyze.failed", error=str(e))
        raise

    publish(
        deployment_id,
        "analyze.done",
        framework=result["framework"],
        confidence=result["confidence"],
    )
    return result


async def _do_analyze(deployment_id: UUID, repo_id: UUID) -> dict:
    """The actual async work — load repo + owner, decrypt token, run the analyzer."""
    async with db_session() as db:
        repo = await db.scalar(select(Repo).where(Repo.id == repo_id))
        if repo is None:
            raise ValueError(f"repo {repo_id} not found")

        owner = await db.scalar(select(User).where(User.id == repo.owner_id))
        if owner is None or not owner.github_access_token_enc:
            raise ValueError(f"owner of repo {repo_id} has no GitHub token")

        token = decrypt(owner.github_access_token_enc)
        gh_owner, gh_name = repo.full_name.split("/", 1)
        branch = repo.default_branch

    publish(str(deployment_id), "analyze.fetching_repo")

    try:
        result = await analyze_repo(token, gh_owner, gh_name, branch=branch)
    finally:
        # Worker process is short-lived (task pool churn), but be tidy with
        # the shared httpx client so we don't leave sockets open.
        await close_github_client()

    return result.model_dump(mode="json")
