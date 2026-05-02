"""builder queue — clone the repo, docker build, push to ECR.

Inputs are read from the Deployment row (status='building' on entry):
  - repo.clone_url + branch
  - the owner's GitHub token (to authenticate the clone)
  - slug (for the ECR image URI)

On success:
  - Deployment.image_uri is set, status flips to 'deploying'
  - We chain into worker.tasks.deploy.run

On failure:
  - status='failed', failed_at_step='build', error_message captured

Long-running: builds can take 5–15 min on first run, ~1 min with cache. The
celery decorator below uses generous time limits to match.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import structlog
from celery import shared_task
from sqlalchemy import select

from shared.core.config import get_settings
from shared.core.database import db_session
from shared.core.security import decrypt
from shared.docker import UnsupportedFrameworkError, generate_dockerfile
from shared.models.deployment import Deployment, DeploymentStatus, DeploymentStep
from shared.models.repo import Repo
from shared.models.user import User
from worker.celery_app import app as celery_app
from worker.progress import publish
from worker.runtime import docker_build, ecr, git_runtime

log = structlog.get_logger(__name__)
_settings = get_settings()


@shared_task(
    name="worker.tasks.build.run",
    bind=True,
    soft_time_limit=1800,  # 30 min soft
    time_limit=2100,       # 35 min hard
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=2,         # builds are expensive; don't retry forever
)
def run(self, deployment_id: str) -> dict:
    """Build the user's Docker image, push to ECR, and chain into deploy."""
    log.info("build.start", deployment_id=deployment_id, task_id=self.request.id)
    publish(deployment_id, "build.started")
    try:
        result = asyncio.run(_do_build(UUID(deployment_id)))
    except Exception as e:
        # On *final* retry failure, mark the row failed. Celery hands us the
        # exception even before the retry-budget exhausts, but we want the row
        # to stay 'building' until we genuinely give up. So: only flip on the
        # last retry.
        if self.request.retries >= (self.max_retries or 0):
            asyncio.run(_mark_failed(UUID(deployment_id), str(e)))
            publish(deployment_id, "build.failed", error=str(e))
        raise

    publish(deployment_id, "build.done", image_uri=result["image_uri"])
    # Chain. send_task by name avoids importing worker.tasks.deploy here, which
    # would create an import cycle with celery_app.
    celery_app.send_task("worker.tasks.deploy.run", args=[deployment_id])
    return result


async def _do_build(deployment_id: UUID) -> dict:
    """Async core. Loads the deployment, clones, builds, pushes, updates row."""
    async with db_session() as db:
        deployment = await db.scalar(select(Deployment).where(Deployment.id == deployment_id))
        if deployment is None:
            raise ValueError(f"deployment {deployment_id} not found")

        repo = await db.scalar(select(Repo).where(Repo.id == deployment.repo_id))
        if repo is None:
            raise ValueError(f"repo {deployment.repo_id} not found")

        owner = await db.scalar(select(User).where(User.id == repo.owner_id))
        if owner is None or not owner.github_access_token_enc:
            raise ValueError(f"owner of repo {repo.id} has no GitHub token")

        token = decrypt(owner.github_access_token_enc)
        slug = deployment.slug
        clone_url = repo.clone_url
        branch = deployment.branch or repo.default_branch
        # Pull the analysis blob now (while we have the session) so the
        # generator can run inside the clone block without a second DB roundtrip.
        analysis = deployment.analysis or {}

        # Mark transition to 'building' (caller may have left us in 'analyzing').
        deployment.status = DeploymentStatus.BUILDING
        await db.flush()

    # 1. Pre-create the ECR repo. We could lazily create on first push, but
    #    doing it here surfaces permission issues fast (before the ~10 min
    #    build) and gives us the ARN to store on the row.
    ecr_arn = ecr.ensure_repository(slug)

    # 2. Get a docker-login token for the registry.
    username, password, registry = ecr.get_authorization()
    docker_build.docker_login(registry=registry, username=username, password=password)

    # 3. Clone the repo (shallow). The context manager cleans up the tempdir
    #    no matter what.
    image_tag = "latest"
    image_uri = _settings.ecr_image_uri(slug, image_tag)
    head_sha: str = ""
    dockerfile_content: str = ""
    dockerfile_was_generated = False
    with git_runtime.shallow_clone(clone_url=clone_url, token=token, branch=branch) as repo_dir:
        head_sha = git_runtime.head_sha(repo_dir)

        # 4. Find or generate the Dockerfile. Repo wins if it ships one — we
        #    never overwrite a hand-written Dockerfile.
        dockerfile = _find_dockerfile(repo_dir)
        if dockerfile is None:
            # 4a. Generate from the analyzer's output. The analyzer task wrote
            #     the AnalysisResult onto deployment.analysis before chaining
            #     into us; if it isn't there we have nothing to base a
            #     Dockerfile on, so we fail fast rather than guess.
            if not analysis:
                raise RuntimeError(
                    "no Dockerfile in repo and no analysis on the deployment row — "
                    "cannot generate. Was analyze.run skipped?"
                )
            try:
                dockerfile_content = generate_dockerfile(analysis)
            except UnsupportedFrameworkError as e:
                # Surface a clean error message; don't retry — re-running
                # won't change the framework.
                raise RuntimeError(str(e)) from e
            dockerfile = repo_dir / "Dockerfile"
            dockerfile.write_text(dockerfile_content, encoding="utf-8")
            dockerfile_was_generated = True
            log.info("build.dockerfile_generated", deployment_id=str(deployment_id), bytes=len(dockerfile_content))
            publish(str(deployment_id), "build.dockerfile_generated")
        else:
            # Capture the repo's Dockerfile too so the detail view can show
            # exactly what we built — same rendering for repo-supplied vs
            # generated, just a flag to tell them apart.
            dockerfile_content = dockerfile.read_text(encoding="utf-8", errors="replace")

        # 5. Build + push. Stream lines into the deployment events stream so
        #    the frontend can show a live log.
        def _on_line(line: str) -> None:
            publish(str(deployment_id), "build.log", line=line)

        docker_build.build_and_push(
            context_dir=repo_dir,
            dockerfile=dockerfile,
            image_uri=image_uri,
            on_log_line=_on_line,
        )

    # 6. Persist the result. Keep the column update tiny — long-lived txns
    #    are bad neighbors here.
    async with db_session() as db:
        deployment = await db.scalar(select(Deployment).where(Deployment.id == deployment_id))
        if deployment is None:
            # Race: somebody deleted it mid-build. Don't error; just exit.
            log.warning("build.deployment_gone", id=str(deployment_id))
            return {"image_uri": image_uri}
        deployment.image_uri = image_uri
        deployment.ecr_repository_arn = ecr_arn
        if head_sha:
            deployment.commit_sha = head_sha
        if dockerfile_content:
            deployment.dockerfile_content = dockerfile_content

    return {
        "image_uri": image_uri,
        "commit_sha": head_sha,
        "dockerfile_generated": dockerfile_was_generated,
    }


def _find_dockerfile(repo_dir: Path) -> Path | None:
    """Locate the Dockerfile. Today only checks the root.

    We could be smarter (look for ./docker/Dockerfile, parse compose files,
    etc.), but real apps put it at the root the vast majority of the time.
    """
    candidate = repo_dir / "Dockerfile"
    return candidate if candidate.is_file() else None


async def _mark_failed(deployment_id: UUID, error: str) -> None:
    async with db_session() as db:
        deployment = await db.scalar(select(Deployment).where(Deployment.id == deployment_id))
        if deployment is None:
            return
        deployment.status = DeploymentStatus.FAILED
        deployment.failed_at_step = DeploymentStep.BUILD
        # Keep error_message bounded — DB column is TEXT but we don't want
        # multi-megabyte stack traces from bad builds.
        deployment.error_message = error[:4000]
        deployment.updated_at = datetime.now(timezone.utc)
