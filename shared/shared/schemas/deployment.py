"""Pydantic schemas for /deployments endpoints.

Three shapes:
  - CreateDeploymentRequest:  what the frontend POSTs
  - DeploymentOut:            what we return (slim — frontend mostly cares
                              about status + url)
  - DeploymentDetailOut:      adds the analysis blob + ARNs for the detail view
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateDeploymentRequest(BaseModel):
    """Request body for POST /deployments."""

    repo_id: UUID
    # Optional override; if absent, the worker uses repo.default_branch.
    branch: str | None = Field(default=None, max_length=255)


class DeploymentOut(BaseModel):
    """List + status response — small enough to poll cheaply."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    repo_id: UUID
    slug: str
    status: str  # serialized as the enum value, e.g. "running"
    framework: str | None
    branch: str | None
    commit_sha: str | None
    url: str | None
    error_message: str | None
    failed_at_step: str | None

    created_at: datetime
    updated_at: datetime
    deployed_at: datetime | None


class DeploymentDetailOut(DeploymentOut):
    """Detail view — adds the full analysis blob + AWS ARNs.

    Useful for the "Deployment details" page. We expose the AWS ARNs so an
    operator can paste them into the AWS console; in a hardened build we'd
    gate this behind an admin role.
    """

    image_uri: str | None
    analysis: dict | None
    dockerfile_content: str | None

    ecr_repository_arn: str | None
    task_definition_arn: str | None
    target_group_arn: str | None
    listener_rule_arn: str | None
    ecs_service_arn: str | None
    route53_record_name: str | None
