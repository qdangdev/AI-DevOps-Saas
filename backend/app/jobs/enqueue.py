"""Thin wrappers the API uses to enqueue Celery tasks.

We address tasks by *string name* instead of importing the worker package, so the
API image doesn't need to import `worker.tasks.*` (and pull in their deps). The
task names below must match the dotted paths Celery auto-registers from the
worker's `include=[...]` list in `worker/celery_app.py`.

Each function returns the Celery `AsyncResult.id` so callers can persist it
alongside the deployment row for status polling.
"""
from __future__ import annotations

from uuid import UUID

from celery import Celery

from shared.core.config import get_settings

_settings = get_settings()

# Standalone Celery client — broker-only, no task registry.
# Sending by name means we don't need to import worker code.
_celery = Celery("ai_devops_saas_api", broker=str(_settings.redis_url))


def _send(name: str, *args: object) -> str:
    result = _celery.send_task(name, args=list(args))
    return result.id


def enqueue_analyze(deployment_id: UUID, repo_id: UUID) -> str:
    return _send("worker.tasks.analyze.run", str(deployment_id), str(repo_id))


def enqueue_dockergen(deployment_id: UUID) -> str:
    return _send("worker.tasks.dockergen.run", str(deployment_id))


def enqueue_build(deployment_id: UUID) -> str:
    return _send("worker.tasks.build.run", str(deployment_id))


def enqueue_deploy(deployment_id: UUID) -> str:
    return _send("worker.tasks.deploy.run", str(deployment_id))


def enqueue_teardown(deployment_id: UUID) -> str:
    """Stop a deployment — release the ECS service, ALB rule + TG, ECR repo, DNS."""
    return _send("worker.tasks.deploy.teardown", str(deployment_id))
