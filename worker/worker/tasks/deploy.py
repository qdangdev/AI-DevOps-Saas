"""deployer queue — register ECS task def, update service, wait for healthy."""
from __future__ import annotations

import structlog
from celery import shared_task

from worker.progress import publish

log = structlog.get_logger(__name__)


@shared_task(
    name="worker.tasks.deploy.run",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def run(self, *, deployment_id: str, image_uri: str, slug: str) -> dict:
    """Push to ECS Fargate and wait for the new tasks to pass health checks.

    TODO:
      1. RegisterTaskDefinition with image_uri + standard env (PORT, etc.).
      2. CreateService on first deploy / UpdateService thereafter.
      3. Attach to shared ALB with host-based routing rule for `{slug}.apps.example.com`.
      4. Poll DescribeServices until runningCount == desiredCount and target health is healthy.
      5. Update Deployment.url and Deployment.status='live'.
    """
    log.info("deploy.start", deployment_id=deployment_id, slug=slug, image_uri=image_uri)
    publish(deployment_id, "deploy.started", {"slug": slug})
    publish(deployment_id, "deploy.healthy", {"url": f"https://{slug}.apps.example.com"})
    return {"status": "stub", "url": f"https://{slug}.apps.example.com"}
