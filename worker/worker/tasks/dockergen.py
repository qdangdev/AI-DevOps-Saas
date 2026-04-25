"""dockergen queue — analysis → Dockerfile via LLM, validate, store."""
from __future__ import annotations

import structlog
from celery import shared_task

from worker.progress import publish

log = structlog.get_logger(__name__)


@shared_task(
    name="worker.tasks.dockergen.run",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def run(self, *, analysis_id: str, deployment_id: str) -> dict:
    """Generate a Dockerfile from an analysis.

    TODO:
      1. Load analysis row.
      2. shared.services.llm.generate_dockerfile(analysis) — to be added.
      3. Lint with `docker buildx imagetools` style validation; sanity-check entrypoints.
      4. Persist to S3 + DB, return artifact id.
    """
    log.info("dockergen.start", analysis_id=analysis_id, task_id=self.request.id)
    publish(deployment_id, "dockergen.started")
    publish(deployment_id, "dockergen.done")
    return {"status": "stub", "analysis_id": analysis_id}
