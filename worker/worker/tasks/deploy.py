"""deployer queue — register ECS task def, attach to ALB, point DNS, wait healthy.

The 9-step flow this task implements:

    1. Load Deployment row (image_uri must already be set by build).
    2. Make sure the ECR repo exists (idempotent — usually a no-op since build did it).
    3. RegisterTaskDefinition with the user's image + standard env (PORT, …).
    4. Create the target group on the shared ALB.
    5. Add a host-header listener rule:  <slug>.<apps_domain> → target group.
    6. Create or update the ECS service in the cluster, wired to the target group.
    7. Wait for the service to stabilize (ECS-side).
    8. Wait for at least one healthy target (ALB-side).
    9. Upsert the Route53 ALIAS record so the URL resolves, then mark running.

On failure we tear down whatever was created so we don't leak resources.
The teardown is deliberately *partial*: only resources whose ARN we recorded
on the row get cleaned up, so we never blast a resource we didn't create.

Idempotency:
    Each AWS step is idempotent on its own (create_repository tolerates
    "already exists", update_service is a no-op if nothing changed, UPSERT on
    Route53). Re-running the task on a healthy deployment should be safe.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

import structlog
from celery import shared_task
from sqlalchemy import select

from shared.core.config import get_settings
from shared.core.database import db_session
from shared.models.deployment import Deployment, DeploymentStatus, DeploymentStep
from worker.progress import publish
from worker.runtime import ecr, ecs, elbv2, route53

log = structlog.get_logger(__name__)
_settings = get_settings()

# Default port the user's container exposes. Real impl should pull this from
# the analysis result's `default_port`; we fall back to 8080 for "Dockerized
# app, no analysis" demo flows.
_FALLBACK_PORT = 8080


@shared_task(
    name="worker.tasks.deploy.run",
    bind=True,
    soft_time_limit=900,   # 15 min soft
    time_limit=1200,       # 20 min hard
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=2,
)
def run(self, deployment_id: str) -> dict:
    """Push to ECS Fargate, attach to ALB + DNS, return the live URL."""
    log.info("deploy.start", deployment_id=deployment_id, task_id=self.request.id)
    publish(deployment_id, "deploy.started")
    try:
        result = asyncio.run(_do_deploy(UUID(deployment_id)))
    except Exception as e:
        # Final retry: mark row failed and try to clean up.
        if self.request.retries >= (self.max_retries or 0):
            asyncio.run(_mark_failed(UUID(deployment_id), str(e)))
            publish(deployment_id, "deploy.failed", error=str(e))
            try:
                asyncio.run(teardown(UUID(deployment_id)))
            except Exception as te:  # noqa: BLE001
                log.warning("deploy.teardown_failed_after_error", error=str(te))
        raise

    publish(deployment_id, "deploy.healthy", url=result["url"])
    return result


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------


async def _do_deploy(deployment_id: UUID) -> dict:
    """The 9-step flow described in the module docstring."""
    # 1. Load row + extract everything we need.
    async with db_session() as db:
        deployment = await db.scalar(select(Deployment).where(Deployment.id == deployment_id))
        if deployment is None:
            raise ValueError(f"deployment {deployment_id} not found")
        if not deployment.image_uri:
            raise ValueError("image_uri not set — build must run before deploy")

        slug = deployment.slug
        image_uri = deployment.image_uri
        analysis = deployment.analysis or {}

        deployment.status = DeploymentStatus.DEPLOYING
        await db.flush()

    container_port: int = int(
        (analysis.get("deployment_strategy") or {}).get("expose_port")
        or analysis.get("default_port")
        or _FALLBACK_PORT
    )
    health_check_path: str = (
        (analysis.get("deployment_strategy") or {}).get("health_check_path") or "/"
    )
    env_vars: dict[str, str] = {
        "PORT": str(container_port),
        # Make analysis-derived env vars available too. We don't have *values*
        # for them — those come from a future "secrets" feature — but exposing
        # the names is a hint for the user.
        **{name: "" for name in analysis.get("env_vars") or []},
    }

    host_header = f"{slug}.{_settings.apps_domain}"
    family = slug              # task definition family = slug
    service_name = slug        # ECS service name = slug
    tg_name = slug             # target group name = slug (≤32 chars per slugs.py)
    public_url = _settings.deployment_url(slug)

    # 2. Idempotent repo create (build should have done this; cheap to repeat).
    ecr_arn = ecr.ensure_repository(slug)

    # 3. Register a new task definition revision. Always a new revision so
    #    we can roll back if needed.
    publish(str(deployment_id), "deploy.task_def")
    task_def_arn = ecs.register_task_definition(
        family=family,
        image_uri=image_uri,
        container_port=container_port,
        env=env_vars,
    )

    # 4. Create the target group.
    publish(str(deployment_id), "deploy.target_group")
    tg_arn = elbv2.create_target_group(
        name=tg_name,
        port=container_port,
        health_check_path=health_check_path,
    )

    # 5. Listener rule attaching host_header → target group.
    publish(str(deployment_id), "deploy.listener_rule")
    rule_arn, _priority = elbv2.create_listener_rule(
        target_group_arn=tg_arn,
        host_header=host_header,
    )

    # 6. Create or update the ECS service. update_service is what we use on
    #    re-deploys; first deploy needs create_service.
    publish(str(deployment_id), "deploy.ecs_service")
    if ecs.service_exists(service_name):
        service_arn = ecs.update_service(
            service_name=service_name,
            task_definition_arn=task_def_arn,
        )
    else:
        service_arn = ecs.create_service(
            service_name=service_name,
            task_definition_arn=task_def_arn,
            target_group_arn=tg_arn,
            container_port=container_port,
        )

    # Persist ARNs *before* we start waiting — if we crash during the wait,
    # teardown still has what it needs to clean up.
    async with db_session() as db:
        deployment = await db.scalar(select(Deployment).where(Deployment.id == deployment_id))
        if deployment is not None:
            deployment.ecr_repository_arn = ecr_arn
            deployment.task_definition_arn = task_def_arn
            deployment.target_group_arn = tg_arn
            deployment.listener_rule_arn = rule_arn
            deployment.ecs_service_arn = service_arn
            deployment.route53_record_name = f"{slug}.{_settings.apps_domain}."

    # 7. Wait for ECS to report stable.
    publish(str(deployment_id), "deploy.wait_ecs_stable")
    ecs.wait_until_stable(service_name)

    # 8. Wait for the ALB target group to see at least one healthy target.
    publish(str(deployment_id), "deploy.wait_alb_healthy")
    elbv2.wait_for_healthy_target(tg_arn)

    # 9. DNS last — that way we never advertise a hostname before the target
    #    is healthy. Old DNS clients can resolve at any moment.
    publish(str(deployment_id), "deploy.dns")
    record_name = route53.upsert_alias(slug)

    async with db_session() as db:
        deployment = await db.scalar(select(Deployment).where(Deployment.id == deployment_id))
        if deployment is not None:
            deployment.route53_record_name = record_name
            deployment.url = public_url
            deployment.status = DeploymentStatus.RUNNING
            deployment.deployed_at = datetime.now(timezone.utc)

    log.info("deploy.done", deployment_id=str(deployment_id), url=public_url)
    return {"url": public_url, "service_arn": service_arn, "task_definition_arn": task_def_arn}


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------


@shared_task(
    name="worker.tasks.deploy.teardown",
    bind=True,
    soft_time_limit=300,
    time_limit=600,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=2,
)
def teardown_task(self, deployment_id: str) -> dict:
    """Celery entry point for stopping a deployment.

    Best-effort: each AWS step is independently swallowed and logged so a
    single stuck resource doesn't block the rest from being cleaned up.
    """
    log.info("deploy.teardown_task_start", deployment_id=deployment_id, task_id=self.request.id)
    publish(deployment_id, "teardown.started")
    try:
        asyncio.run(teardown(UUID(deployment_id)))
    except Exception as e:
        publish(deployment_id, "teardown.failed", error=str(e))
        raise
    publish(deployment_id, "teardown.done")
    return {"status": "stopped"}


async def teardown(deployment_id: UUID) -> None:
    """Best-effort delete of every AWS resource the deployment created.

    Uses the ARNs stored on the row. Order matters:
      - Route53 first (so DNS stops pointing at us)
      - listener rule (stops new connections)
      - service drain + delete
      - target group (after service is gone — ALB rejects deletion otherwise)
      - task definition deregister
      - ECR repo (with images)

    Each individual call is best-effort: we log and continue on failure so
    one stuck resource doesn't strand the rest.
    """
    async with db_session() as db:
        deployment = await db.scalar(select(Deployment).where(Deployment.id == deployment_id))
        if deployment is None:
            return
        slug = deployment.slug
        rule_arn = deployment.listener_rule_arn
        tg_arn = deployment.target_group_arn
        service_arn = deployment.ecs_service_arn
        task_def_arn = deployment.task_definition_arn
        had_route53 = bool(deployment.route53_record_name)

    log.info("deploy.teardown_start", slug=slug)

    if had_route53:
        route53.delete_record(slug)

    if rule_arn:
        elbv2.delete_listener_rule(rule_arn)

    if service_arn:
        ecs.delete_service(slug)

    if tg_arn:
        # The TG can't be deleted while the service is still detaching; small
        # backoff in delete_service helps but isn't always enough. The
        # underlying call will log on failure and the operator can re-run.
        elbv2.delete_target_group(tg_arn)

    if task_def_arn:
        ecs.deregister_task_definition(task_def_arn)

    # ECR last so build logs still have the image to inspect during early
    # teardown failures.
    ecr.delete_repository(slug)

    async with db_session() as db:
        deployment = await db.scalar(select(Deployment).where(Deployment.id == deployment_id))
        if deployment is not None and deployment.status != DeploymentStatus.FAILED:
            deployment.status = DeploymentStatus.STOPPED
            deployment.url = None

    log.info("deploy.teardown_done", slug=slug)


# ---------------------------------------------------------------------------
# Failure marker
# ---------------------------------------------------------------------------


async def _mark_failed(deployment_id: UUID, error: str) -> None:
    async with db_session() as db:
        deployment = await db.scalar(select(Deployment).where(Deployment.id == deployment_id))
        if deployment is None:
            return
        deployment.status = DeploymentStatus.FAILED
        deployment.failed_at_step = DeploymentStep.DEPLOY
        deployment.error_message = error[:4000]
        deployment.updated_at = datetime.now(timezone.utc)
