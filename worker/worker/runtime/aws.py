"""boto3 client factory.

Centralizes client creation so we (a) configure retries + timeouts in one place
and (b) can swap to a per-request session in tests via the `session` arg.

Why module-level singletons:
  - boto3 clients are thread-safe and expensive to create (auth chain, JSON
    model load). Celery's prefork pool reuses processes for many tasks, so we
    create the clients once at import time and reuse.
  - Exception: in tests we monkey-patch `_get_session` to return a mocked
    session, then call `reset_clients()` to clear the cache.
"""
from __future__ import annotations

from functools import lru_cache

import boto3
from botocore.config import Config

from shared.core.config import get_settings

_settings = get_settings()

# Standard retry posture for our calls. ECS / ELBv2 / Route53 all have eventual
# consistency that "looks like" a transient error — adaptive mode handles this
# better than the legacy "standard" strategy.
_BOTO_CONFIG = Config(
    region_name=_settings.aws_region,
    retries={"max_attempts": 6, "mode": "adaptive"},
    connect_timeout=10,
    read_timeout=60,
    user_agent_extra="ai-devops-saas-worker/0.1",
)


def _session() -> boto3.session.Session:
    return boto3.session.Session()


@lru_cache(maxsize=None)
def _client(service: str):
    return _session().client(service, config=_BOTO_CONFIG)


def ecr():
    return _client("ecr")


def ecs():
    return _client("ecs")


def elbv2():
    return _client("elbv2")


def route53():
    return _client("route53")


def sts():
    return _client("sts")


def reset_clients() -> None:
    """Test hook — clear the cached clients so monkey-patched sessions take effect."""
    _client.cache_clear()
