"""Celery app — broker config, queue routing, retry policy.

Run with:
    celery -A worker.celery_app:app worker -Q analyzer,dockergen,builder,deployer --loglevel=info

In production each pool runs as its own ECS service with -Q matching its queue, so
analyzer/dockergen/builder/deployer scale independently.
"""
from __future__ import annotations

from celery import Celery

from shared.core.config import get_settings
from shared.core.logging import configure_logging

settings = get_settings()
configure_logging()

app = Celery(
    "ai_devops_saas",
    broker=str(settings.redis_url),
    backend=str(settings.redis_url),
    include=[
        "worker.tasks.analyze",
        "worker.tasks.dockergen",
        "worker.tasks.build",
        "worker.tasks.deploy",
    ],
)

# --- Routing --------------------------------------------------------------
# Each task is published to a named queue. Worker pools subscribe to the queues
# they're sized for; never run a long build on the analyzer pool.
app.conf.task_routes = {
    "worker.tasks.analyze.*": {"queue": "analyzer"},
    "worker.tasks.dockergen.*": {"queue": "dockergen"},
    "worker.tasks.build.*": {"queue": "builder"},
    "worker.tasks.deploy.*": {"queue": "deployer"},
}

# --- Reliability ----------------------------------------------------------
# acks_late + reject_on_worker_lost: if a worker dies mid-task, the broker
# re-delivers it. Combined with idempotency keys on the task side, this is safe
# at-least-once behavior. Don't enable acks_late for non-idempotent tasks.
app.conf.task_acks_late = True
app.conf.task_reject_on_worker_lost = True
app.conf.worker_prefetch_multiplier = 1  # don't hoard tasks; fairness > throughput here

# --- Retries --------------------------------------------------------------
app.conf.task_default_retry_delay = 30
app.conf.task_annotations = {
    "*": {"max_retries": 3},
}

# --- Time limits ----------------------------------------------------------
# soft: task is sent SIGTERM (cleanup); hard: SIGKILL.
# Builds can run long, so the builder queue overrides this in its task config.
app.conf.task_soft_time_limit = 600   # 10 min default
app.conf.task_time_limit = 900        # 15 min hard kill

# Serialization: JSON only — no pickle.
app.conf.task_serializer = "json"
app.conf.result_serializer = "json"
app.conf.accept_content = ["json"]

# Timezone
app.conf.timezone = "UTC"
app.conf.enable_utc = True
