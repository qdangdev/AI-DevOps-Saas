"""ECS helpers — register task definitions, create/update Fargate services, wait, tear down.

We deploy one ECS service per deployment slug, on the shared cluster + ALB.
The service runs awsvpc-mode tasks in private subnets; the ALB target group
(created in elbv2.py) is what brings traffic to them.

Health-check semantics:
  - "service stable" = ECS got runningCount up to desiredCount.
  - "ALB healthy"    = the target group reports HEALTHY for at least one task.

Both have to be true before we mark the deployment running. ECS-stable alone
isn't enough — a container can be running (passing the docker process check)
while still 502-ing because the app hasn't bound to the port yet.
"""
from __future__ import annotations

import time
from typing import Any

import structlog
from botocore.exceptions import ClientError, WaiterError

from shared.core.config import get_settings
from worker.runtime.aws import ecs

log = structlog.get_logger(__name__)
_settings = get_settings()


class ECSError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Task definition
# ---------------------------------------------------------------------------


def register_task_definition(
    *,
    family: str,
    image_uri: str,
    container_port: int,
    env: dict[str, str] | None = None,
) -> str:
    """Register a new revision and return its ARN.

    `family` is per-slug so revisions accumulate per deployment, which gives us
    rollback for free (point the service at an older revision).
    """
    container_def: dict[str, Any] = {
        "name": "app",
        "image": image_uri,
        "essential": True,
        "portMappings": [{"containerPort": container_port, "protocol": "tcp"}],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": _settings.ecs_log_group,
                "awslogs-region": _settings.aws_region,
                # Stream prefix scoped to the family so we can find this
                # deployment's logs without a wildcard scan.
                "awslogs-stream-prefix": family,
                # Auto-create the log stream on first task start. The group
                # itself must exist — that's a Terraform thing.
                "awslogs-create-group": "false",
            },
        },
    }
    if env:
        container_def["environment"] = [{"name": k, "value": v} for k, v in env.items()]

    try:
        resp = ecs().register_task_definition(
            family=family,
            networkMode="awsvpc",
            requiresCompatibilities=["FARGATE"],
            cpu=_settings.ecs_task_cpu,
            memory=_settings.ecs_task_memory,
            executionRoleArn=_settings.ecs_task_execution_role_arn,
            taskRoleArn=_settings.ecs_task_role_arn,
            containerDefinitions=[container_def],
        )
        arn = resp["taskDefinition"]["taskDefinitionArn"]
        log.info("ecs.task_def_registered", family=family, arn=arn)
        return arn
    except ClientError as e:
        raise ECSError(f"register_task_definition failed for {family}: {e}") from e


def deregister_task_definition(arn: str) -> None:
    """Deregister a task definition revision (best-effort)."""
    try:
        ecs().deregister_task_definition(taskDefinition=arn)
        log.info("ecs.task_def_deregistered", arn=arn)
    except ClientError as e:
        log.warning("ecs.task_def_deregister_failed", arn=arn, error=str(e))


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def create_service(
    *,
    service_name: str,
    task_definition_arn: str,
    target_group_arn: str,
    container_port: int,
    desired_count: int = 1,
) -> str:
    """Create a Fargate service wired to the given target group. Returns service ARN."""
    if not _settings.ecs_subnet_ids or not _settings.ecs_security_group_ids:
        raise ECSError("ECS subnets/security groups not configured")

    try:
        resp = ecs().create_service(
            cluster=_settings.ecs_cluster,
            serviceName=service_name,
            taskDefinition=task_definition_arn,
            desiredCount=desired_count,
            launchType="FARGATE",
            platformVersion="LATEST",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": list(_settings.ecs_subnet_ids),
                    "securityGroups": list(_settings.ecs_security_group_ids),
                    # Public IP off — tasks reach the internet via NAT.
                    "assignPublicIp": "DISABLED",
                }
            },
            loadBalancers=[{
                "targetGroupArn": target_group_arn,
                "containerName": "app",
                "containerPort": container_port,
            }],
            # Give the container 60s to start passing health checks before
            # the ALB starts grading it. Lots of frameworks (Django, Spring)
            # take a few seconds to bind.
            healthCheckGracePeriodSeconds=60,
            deploymentConfiguration={
                "maximumPercent": 200,
                "minimumHealthyPercent": 100,
                # Roll back automatically if the new task set never goes healthy.
                "deploymentCircuitBreaker": {"enable": True, "rollback": True},
            },
            propagateTags="SERVICE",
            enableExecuteCommand=False,
        )
        arn = resp["service"]["serviceArn"]
        log.info("ecs.service_created", name=service_name, arn=arn)
        return arn
    except ClientError as e:
        raise ECSError(f"create_service failed for {service_name}: {e}") from e


def update_service(
    *,
    service_name: str,
    task_definition_arn: str,
    desired_count: int | None = None,
) -> str:
    """Update an existing service to a new task def revision. Returns service ARN."""
    kwargs: dict[str, Any] = {
        "cluster": _settings.ecs_cluster,
        "service": service_name,
        "taskDefinition": task_definition_arn,
        "forceNewDeployment": True,
    }
    if desired_count is not None:
        kwargs["desiredCount"] = desired_count
    try:
        resp = ecs().update_service(**kwargs)
        arn = resp["service"]["serviceArn"]
        log.info("ecs.service_updated", name=service_name, arn=arn)
        return arn
    except ClientError as e:
        raise ECSError(f"update_service failed for {service_name}: {e}") from e


def service_exists(service_name: str) -> bool:
    """Cheap existence check — used to choose create vs update."""
    try:
        resp = ecs().describe_services(
            cluster=_settings.ecs_cluster,
            services=[service_name],
        )
    except ClientError as e:
        raise ECSError(f"describe_services failed for {service_name}: {e}") from e

    for svc in resp.get("services", []):
        if svc.get("serviceName") == service_name and svc.get("status") != "INACTIVE":
            return True
    return False


def wait_until_stable(service_name: str, timeout_seconds: int | None = None) -> None:
    """Block until ECS reports the service stable, or raise on timeout.

    "Stable" = runningCount == desiredCount and no in-flight deployments.
    Boto's built-in waiter polls every 15s for up to 40 attempts (10 min);
    we cap with our own deadline so we don't double the wait.
    """
    timeout = timeout_seconds or _settings.deploy_wait_timeout_seconds
    waiter = ecs().get_waiter("services_stable")
    # boto waiter delay/max-attempts: keep total ≤ our timeout.
    delay = _settings.deploy_wait_interval_seconds
    max_attempts = max(1, timeout // delay)
    try:
        waiter.wait(
            cluster=_settings.ecs_cluster,
            services=[service_name],
            WaiterConfig={"Delay": delay, "MaxAttempts": max_attempts},
        )
    except WaiterError as e:
        raise ECSError(f"service {service_name} did not stabilize within {timeout}s: {e}") from e


def delete_service(service_name: str) -> None:
    """Set desiredCount=0 then delete. Best-effort — used during teardown.

    Why two steps: ECS won't let you delete a service with running tasks
    unless you pass `force=True`. We prefer the graceful path so the ALB
    has time to drain, but if the user already scaled to 0 the first call
    is a no-op.
    """
    try:
        ecs().update_service(
            cluster=_settings.ecs_cluster,
            service=service_name,
            desiredCount=0,
        )
        # Brief drain — don't block the worker for long.
        time.sleep(5)
    except ClientError as e:
        # Service might not exist; fall through to delete which will surface that.
        log.warning("ecs.service_drain_failed", name=service_name, error=str(e))

    try:
        ecs().delete_service(
            cluster=_settings.ecs_cluster,
            service=service_name,
            force=True,
        )
        log.info("ecs.service_deleted", name=service_name)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("ServiceNotFoundException", "ServiceNotActiveException"):
            log.info("ecs.service_already_gone", name=service_name)
            return
        log.warning("ecs.service_delete_failed", name=service_name, error=str(e))
