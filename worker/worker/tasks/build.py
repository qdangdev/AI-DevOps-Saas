"""builder queue — `docker build` + push to ECR. Long-running, CPU+disk heavy.

Override the default time limit since real builds can run 5–15 minutes.
"""
from __future__ import annotations

import structlog
from celery import shared_task

from worker.progress import publish

log = structlog.get_logger(__name__)


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
def run(self, *, dockerfile_id: str, deployment_id: str, image_tag: str) -> dict:
    """Build the user's Docker image and push to ECR.

    TODO:
      1. Pull Dockerfile from S3.
      2. Run `docker buildx build` with BuildKit + S3 remote cache.
      3. Tee build output to publish(... 'build.log', {'line': ...}) line-by-line.
      4. Tag and push to ECR. Return image URI.
    """
    log.info("build.start", dockerfile_id=dockerfile_id, image_tag=image_tag)
    publish(deployment_id, "build.started", {"image_tag": image_tag})
    publish(deployment_id, "build.done", {"image_uri": f"<ecr-uri>:{image_tag}"})
    return {"status": "stub", "image_tag": image_tag}
