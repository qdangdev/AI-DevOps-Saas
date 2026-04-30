"""analyzer queue — fetch repo metadata, ask Claude to characterize it, publish events.

Celery tasks are sync, but the analyzer + DB are async. We use `asyncio.run`
to bridge — the task itself is short-lived and doesn't share state with
anything else in the worker process, so a fresh event loop per task is fine.

Args (positional, matching backend.app.jobs.enqueue.enqueue_analyze):
  deployment_id — for progress events on the deployment:{id}:events stream
  repo_id       — UUID of the repos row to analyze

On success, persists the analysis onto the Deployment row (status flips to
'building'), then chains into worker.tasks.build.run.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

import structlog
from celery import shared_task
from sqlalchemy import select

from shared.analysis import analyze_repo
from shared.core.database import db_session
from shared.core.security import decrypt
from shared.models.deployment import Deployment, DeploymentStatus, DeploymentStep
from shared.models.repo import Repo
from shared.models.user import User
from shared.services.github import close_client as close_github_client
from worker.celery_app import app as celery_app
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
    """Analyze a repo, persist the result, and chain into build."""
    log.info("analyze.start", repo_id=repo_id, deployment_id=deployment_id, task_id=self.request.id)
    publish(deployment_id, "analyze.started")
    try:
        result = asyncio.run(_do_analyze(UUID(deployment_id), UUID(repo_id)))
    except Exception as e:
        # Only flip the row to 'failed' on the *final* retry — earlier retries
        # leave it in 'analyzing' so the UI shows "still working" instead of
        # bouncing red→yellow→red.
        if self.request.retries >= (self.max_retries or 0):
            asyncio.run(_mark_failed(UUID(deployment_id), str(e)))
            publish(deployment_id, "analyze.failed", error=str(e))
        raise

    publish(
        deployment_id,
        "analyze.done",
        framework=result["framework"],
        confidence=result["confidence"],
    )
    # Chain into build. send_task by name avoids a hard import of the build
    # module in the analyzer pool (which doesn't have docker installed).
    celery_app.send_task("worker.tasks.build.run", args=[deployment_id])
    return result


async def _do_analyze(deployment_id: UUID, repo_id: UUID) -> dict:
    """The actual async work — load repo + owner, decrypt token, run the analyzer,
    persist the analysis onto the deployment row."""
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

        # Mark in-progress so the UI shows movement.
        deployment = await db.scalar(select(Deployment).where(Deployment.id == deployment_id))
        if deployment is not None and deployment.status == DeploymentStatus.PENDING:
            deployment.status = DeploymentStatus.ANALYZING
            await db.flush()

    publish(str(deployment_id), "analyze.fetching_repo")

    try:
        result = await analyze_repo(token, gh_owner, gh_name, branch=branch)
    finally:
        # Worker process is short-lived (task pool churn), but be tidy with
        # the shared httpx client so we don't leave sockets open.
        await close_github_client()

    payload = result.model_dump(mode="json")

    # Persist analysis onto the deployment row + advance status. The next
    # task (build) reads from this row — that's the contract between stages.
    async with db_session() as db:
        deployment = await db.scalar(select(Deployment).where(Deployment.id == deployment_id))
        if deployment is not None:
            deployment.analysis = payload
            deployment.framework = payload.get("framework")
            deployment.branch = branch
            deployment.status = DeploymentStatus.BUILDING

    return payload


async def _mark_failed(deployment_id: UUID, error: str) -> None:
    async with db_session() as db:
        deployment = await db.scalar(select(Deployment).where(Deployment.id == deployment_id))
        if deployment is None:
            return
        deployment.status = DeploymentStatus.FAILED
        deployment.failed_at_step = DeploymentStep.ANALYZE
        deployment.error_message = error[:4000]
        deployment.updated_at = datetime.now(timezone.utc)
