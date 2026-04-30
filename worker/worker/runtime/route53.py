"""Route53 helpers — point a per-deployment subdomain at the shared ALB.

We use ALIAS records (not CNAMEs) because:
  - ALIAS resolves at the Route53 layer, so apex/subdomain semantics work the
    same and there's no extra DNS hop.
  - AWS doesn't charge for ALIAS lookups against AWS targets.

Each deployment owns one record: <slug>.<apps_domain>.
"""
from __future__ import annotations

import structlog
from botocore.exceptions import ClientError

from shared.core.config import get_settings
from worker.runtime.aws import route53

log = structlog.get_logger(__name__)
_settings = get_settings()


class Route53Error(RuntimeError):
    pass


def _record_name(slug: str) -> str:
    """Fully-qualified record name including trailing dot."""
    return f"{slug}.{_settings.apps_domain}."


def _change_batch(action: str, slug: str) -> dict:
    if not _settings.alb_dns_name or not _settings.alb_hosted_zone_id:
        raise Route53Error("ALB DNS name / hosted zone not configured")

    return {
        "Changes": [{
            "Action": action,
            "ResourceRecordSet": {
                "Name": _record_name(slug),
                "Type": "A",
                "AliasTarget": {
                    "HostedZoneId": _settings.alb_hosted_zone_id,
                    "DNSName": _settings.alb_dns_name,
                    # Disabled — we don't need health-check failover for
                    # demo deployments. Keep simple.
                    "EvaluateTargetHealth": False,
                },
            },
        }]
    }


def upsert_alias(slug: str) -> str:
    """Create or update the ALIAS record for the deployment. Returns the record name.

    UPSERT lets us re-run idempotently — same effect whether or not the record
    already exists.
    """
    if not _settings.hosted_zone_id:
        raise Route53Error("Route53 hosted zone id not configured")
    try:
        route53().change_resource_record_sets(
            HostedZoneId=_settings.hosted_zone_id,
            ChangeBatch=_change_batch("UPSERT", slug),
        )
    except ClientError as e:
        raise Route53Error(f"failed to upsert DNS record for {slug}: {e}") from e

    name = _record_name(slug)
    log.info("route53.upsert", record=name)
    return name


def delete_record(slug: str) -> None:
    """Best-effort delete. Used during teardown.

    Route53 errors if the record doesn't exist with the exact AliasTarget
    we send, so we tolerate `InvalidChangeBatch` (record already gone) here.
    """
    if not _settings.hosted_zone_id:
        return
    try:
        route53().change_resource_record_sets(
            HostedZoneId=_settings.hosted_zone_id,
            ChangeBatch=_change_batch("DELETE", slug),
        )
        log.info("route53.deleted", record=_record_name(slug))
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "InvalidChangeBatch":
            log.info("route53.record_already_gone", record=_record_name(slug))
            return
        log.warning("route53.delete_failed", record=_record_name(slug), error=str(e))
