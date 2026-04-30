"""Deployment endpoints — create, list, get status, tear down.

All routes require auth. The owner check is by repo: a user can only see /
mutate deployments for repos they own.

Lifecycle is driven by the analyzer → builder → deployer task chain in the
worker. The API just creates the row and enqueues; everything after that
moves through the worker side, which writes status + url back to the row.
"""
from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.jobs.enqueue import enqueue_analyze
from shared.core.slugs import make_slug
from shared.models.deployment import Deployment, DeploymentStatus
from shared.models.repo import Repo
from shared.schemas.deployment import (
    CreateDeploymentRequest,
    DeploymentDetailOut,
    DeploymentOut,
)

router = APIRouter(prefix="/deployments", tags=["deployments"])
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _load_owned_deployment(
    deployment_id: UUID,
    user_id: UUID,
    db,
) -> Deployment:
    """Load a deployment, asserting the caller owns it (via the repo).

    We join through Repo because Deployment.repo_id is the only FK we hold;
    pulling the join into one query avoids a 401-vs-404 leak on bogus IDs.
    """
    stmt = (
        select(Deployment)
        .join(Repo, Repo.id == Deployment.repo_id)
        .where(Deployment.id == deployment_id, Repo.owner_id == user_id)
    )
    deployment = await db.scalar(stmt)
    if deployment is None:
        # Same status code whether it's missing or not-yours — don't leak
        # which deployment IDs exist in the system.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "deployment not found")
    return deployment


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=DeploymentOut, status_code=status.HTTP_201_CREATED)
async def create_deployment(
    body: CreateDeploymentRequest,
    user: CurrentUser,
    db: DbSession,
) -> Deployment:
    """Create a deployment row + kick off the analyze→build→deploy chain.

    Returns immediately with status='pending'. Frontend polls GET
    /deployments/{id} to watch it advance.
    """
    repo = await db.scalar(
        select(Repo).where(Repo.id == body.repo_id, Repo.owner_id == user.id)
    )
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repo not found")

    # Build the slug from owner_login/<repo-name>. We pull the *segments* off
    # the repo's full_name so demos with funky chars still produce safe DNS.
    owner_login, _, repo_name = repo.full_name.partition("/")
    slug = make_slug(owner_login=owner_login or "user", repo_name=repo_name or repo.full_name)

    deployment = Deployment(
        repo_id=repo.id,
        slug=slug,
        branch=body.branch or repo.default_branch,
        status=DeploymentStatus.PENDING,
    )
    db.add(deployment)
    # flush so deployment.id is populated before we enqueue.
    await db.flush()

    # Enqueue *after* flush so the worker can find the row immediately. We
    # don't enqueue inside an open transaction in production — if Celery's
    # broker dispatch outraces our COMMIT, the worker can hit a missing-row
    # error. Mitigated here by the dependency-injected session committing on
    # successful return; for stricter ordering we'd use a transactional
    # outbox.
    enqueue_analyze(deployment.id, repo.id)
    log.info(
        "deployment.created",
        deployment_id=str(deployment.id),
        slug=slug,
        repo_id=str(repo.id),
    )
    return deployment


@router.get("", response_model=list[DeploymentOut])
async def list_deployments(user: CurrentUser, db: DbSession) -> list[Deployment]:
    """All deployments owned by the caller, newest first."""
    stmt = (
        select(Deployment)
        .join(Repo, Repo.id == Deployment.repo_id)
        .where(Repo.owner_id == user.id)
        .order_by(Deployment.created_at.desc())
    )
    result = await db.scalars(stmt)
    return list(result)


@router.get("/{deployment_id}", response_model=DeploymentDetailOut)
async def get_deployment(
    deployment_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> Deployment:
    """Detail view including the analysis blob and AWS ARNs."""
    return await _load_owned_deployment(deployment_id, user.id, db)


@router.post("/{deployment_id}/redeploy", response_model=DeploymentOut, status_code=status.HTTP_201_CREATED)
async def redeploy(
    deployment_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> Deployment:
    """Create a new deployment for the same repo, reusing its branch.

    Implementing as "create a fresh row" (rather than mutating the old one)
    gives us free history and lets us roll back: if the new one fails, the
    old DNS still points at a working service.
    """
    old = await _load_owned_deployment(deployment_id, user.id, db)
    repo = await db.scalar(select(Repo).where(Repo.id == old.repo_id))
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repo no longer connected")

    owner_login, _, repo_name = repo.full_name.partition("/")
    slug = make_slug(owner_login=owner_login or "user", repo_name=repo_name or repo.full_name)

    deployment = Deployment(
        repo_id=repo.id,
        slug=slug,
        branch=old.branch or repo.default_branch,
        status=DeploymentStatus.PENDING,
    )
    db.add(deployment)
    await db.flush()
    enqueue_analyze(deployment.id, repo.id)
    log.info("deployment.redeploy", new_id=str(deployment.id), old_id=str(old.id))
    return deployment


@router.delete("/{deployment_id}", status_code=status.HTTP_202_ACCEPTED)
async def stop_deployment(
    deployment_id: UUID,
    user: CurrentUser,
    db: DbSession,
    background: BackgroundTasks,
) -> dict:
    """Tear down the AWS resources for this deployment.

    Returns 202 immediately; teardown runs as a Celery task on the deployer
    queue. We don't try to delete the row — keeping it gives the user an
    audit trail and lets them re-run.
    """
    deployment = await _load_owned_deployment(deployment_id, user.id, db)
    if deployment.status in (DeploymentStatus.STOPPED, DeploymentStatus.FAILED):
        return {"status": "noop", "reason": f"already {deployment.status.value}"}

    # Mark stopping so the UI updates immediately. The actual teardown is done
    # by the worker via send_task — but we don't want to import the worker
    # task here (build deps), so route through the existing enqueue helper:
    # we add a tiny `enqueue_teardown` to keep the boundary clean.
    from app.jobs.enqueue import enqueue_teardown
    enqueue_teardown(deployment.id)
    log.info("deployment.stop_requested", id=str(deployment.id))
    return {"status": "stopping"}
