"""Publishes per-deployment status events to a Redis stream.

Frontends (via the realtime gateway) subscribe to `deployment:{id}:events` and see
status transitions and log lines as they happen.
"""
from __future__ import annotations

import json
from typing import Any

import redis
import structlog

from shared.core.config import get_settings

log = structlog.get_logger(__name__)
settings = get_settings()

# Sync client — we're inside a Celery task, no event loop.
_redis: redis.Redis | None = None


def _client() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(str(settings.redis_url), decode_responses=True)
    return _redis


def publish(deployment_id: str, event: str, data: dict[str, Any] | None = None) -> None:
    """Append an event to deployment:{id}:events stream.

    event examples: "analyze.started", "analyze.done", "build.log",
                    "deploy.healthy", "failed".
    """
    payload = {"event": event, "data": json.dumps(data or {})}
    try:
        _client().xadd(f"deployment:{deployment_id}:events", payload, maxlen=10_000)
    except redis.RedisError:
        # Don't fail the job because we couldn't publish a status event.
        log.warning("progress.publish_failed", deployment_id=deployment_id, event=event)
