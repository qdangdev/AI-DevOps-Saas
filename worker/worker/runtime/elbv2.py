"""ALB helpers — target group, listener rule, health-check polling.

Per-deployment we add:
  1. A target group (one per ECS service, using IP target type for awsvpc).
  2. A listener rule on the shared HTTPS listener with host_header = <slug>.<apps_domain>.

Tear-down deletes them in reverse order. The shared ALB itself is owned by
Terraform; we never touch it.
"""
from __future__ import annotations

import random
import time

import structlog
from botocore.exceptions import ClientError

from shared.core.config import get_settings
from worker.runtime.aws import elbv2

log = structlog.get_logger(__name__)
_settings = get_settings()


class ALBError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Target group
# ---------------------------------------------------------------------------


def create_target_group(
    *,
    name: str,
    port: int,
    health_check_path: str = "/",
) -> str:
    """Create a TG for our awsvpc Fargate tasks. Returns the ARN.

    We use IP target type because awsvpc tasks register by ENI IP, not instance.
    The TG name must be ≤32 chars and unique within the account — caller
    should pass a slug-derived name.
    """
    if not _settings.alb_vpc_id:
        raise ALBError("ALB VPC id not configured")
    if len(name) > 32:
        raise ALBError(f"target group name too long ({len(name)} > 32): {name}")

    try:
        resp = elbv2().create_target_group(
            Name=name,
            Protocol="HTTP",
            Port=port,
            VpcId=_settings.alb_vpc_id,
            TargetType="ip",
            HealthCheckProtocol="HTTP",
            HealthCheckPath=health_check_path,
            HealthCheckIntervalSeconds=15,
            HealthCheckTimeoutSeconds=5,
            HealthyThresholdCount=2,
            UnhealthyThresholdCount=3,
            Matcher={"HttpCode": "200-399"},
        )
        arn = resp["TargetGroups"][0]["TargetGroupArn"]
        log.info("alb.tg_created", name=name, arn=arn)
        return arn
    except ClientError as e:
        raise ALBError(f"create_target_group failed for {name}: {e}") from e


def delete_target_group(arn: str) -> None:
    """Best-effort delete. The TG must already be detached from any listener rule."""
    try:
        elbv2().delete_target_group(TargetGroupArn=arn)
        log.info("alb.tg_deleted", arn=arn)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "TargetGroupNotFound":
            return
        log.warning("alb.tg_delete_failed", arn=arn, error=str(e))


def wait_for_healthy_target(target_group_arn: str, timeout_seconds: int | None = None) -> None:
    """Poll until the TG has at least one HEALTHY target, or raise on timeout.

    ECS reports services_stable as soon as the desired count is reached, but
    that's *task* stability — the ALB's view of health is what actually
    determines whether traffic flows. We need both.
    """
    deadline = time.monotonic() + (timeout_seconds or _settings.deploy_wait_timeout_seconds)
    interval = _settings.deploy_wait_interval_seconds

    while time.monotonic() < deadline:
        try:
            resp = elbv2().describe_target_health(TargetGroupArn=target_group_arn)
        except ClientError as e:
            raise ALBError(f"describe_target_health failed: {e}") from e

        states = [
            t.get("TargetHealth", {}).get("State")
            for t in resp.get("TargetHealthDescriptions", [])
        ]
        if any(s == "healthy" for s in states):
            log.info("alb.tg_healthy", arn=target_group_arn, states=states)
            return
        log.info("alb.tg_polling", arn=target_group_arn, states=states)
        time.sleep(interval)

    raise ALBError(f"target group {target_group_arn} never reported healthy within timeout")


# ---------------------------------------------------------------------------
# Listener rule
# ---------------------------------------------------------------------------


def _allocate_priority() -> int:
    """Pick an unused listener-rule priority in our reserved range.

    ALB rule priorities are 1..50000 and must be unique per listener. We pick
    a random number in our reserved window and retry on conflict. With 40k
    slots and a few hundred deployments this is collision-free in practice.
    """
    return random.randint(_settings.alb_rule_priority_min, _settings.alb_rule_priority_max)


def create_listener_rule(
    *,
    target_group_arn: str,
    host_header: str,
    max_attempts: int = 5,
) -> tuple[str, int]:
    """Add a host-header rule that forwards to the target group.

    Returns (rule_arn, priority). We retry on PriorityInUse since priorities
    are randomized — if two deploys race we want to back off and pick again.
    """
    if not _settings.alb_https_listener_arn:
        raise ALBError("ALB HTTPS listener ARN not configured")

    last_err: Exception | None = None
    for _ in range(max_attempts):
        priority = _allocate_priority()
        try:
            resp = elbv2().create_rule(
                ListenerArn=_settings.alb_https_listener_arn,
                Priority=priority,
                Conditions=[{"Field": "host-header", "HostHeaderConfig": {"Values": [host_header]}}],
                Actions=[{"Type": "forward", "TargetGroupArn": target_group_arn}],
            )
            arn = resp["Rules"][0]["RuleArn"]
            log.info("alb.rule_created", arn=arn, host=host_header, priority=priority)
            return arn, priority
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code == "PriorityInUse":
                last_err = e
                continue
            raise ALBError(f"create_rule failed for {host_header}: {e}") from e

    raise ALBError(f"could not allocate listener priority after {max_attempts} attempts: {last_err}")


def delete_listener_rule(rule_arn: str) -> None:
    """Best-effort delete."""
    try:
        elbv2().delete_rule(RuleArn=rule_arn)
        log.info("alb.rule_deleted", arn=rule_arn)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "RuleNotFound":
            return
        log.warning("alb.rule_delete_failed", arn=rule_arn, error=str(e))
