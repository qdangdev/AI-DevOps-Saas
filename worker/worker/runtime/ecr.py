"""ECR helpers — create per-deployment repo, get docker login token.

We use one repo per deployment slug (instead of one repo per user with tags
per deployment) so:
  - Retention policies can be set per-deployment.
  - Deleting a deployment deletes exactly its images.
  - There's no risk of one user's tag stomping another's.
"""
from __future__ import annotations

import base64

import structlog
from botocore.exceptions import ClientError

from shared.core.config import get_settings
from worker.runtime.aws import ecr

log = structlog.get_logger(__name__)
_settings = get_settings()


class ECRError(RuntimeError):
    pass


def ensure_repository(slug: str) -> str:
    """Create the ECR repo for this deployment if it doesn't exist. Returns its ARN.

    Idempotent: a `RepositoryAlreadyExistsException` is treated as success and
    we re-describe to fetch the ARN.
    """
    name = _settings.ecr_repo_name(slug)
    try:
        resp = ecr().create_repository(
            repositoryName=name,
            imageScanningConfiguration={"scanOnPush": True},
            imageTagMutability="IMMUTABLE",
            encryptionConfiguration={"encryptionType": "AES256"},
        )
        arn = resp["repository"]["repositoryArn"]
        log.info("ecr.repo_created", name=name, arn=arn)
        return arn
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "RepositoryAlreadyExistsException":
            resp = ecr().describe_repositories(repositoryNames=[name])
            arn = resp["repositories"][0]["repositoryArn"]
            log.info("ecr.repo_exists", name=name, arn=arn)
            return arn
        raise ECRError(f"failed to create ECR repo {name}: {e}") from e


def get_authorization() -> tuple[str, str, str]:
    """Fetch a short-lived ECR docker login. Returns (username, password, registry_url).

    The token is base64("AWS:<password>") and lives for 12 hours. We re-fetch
    on every build to keep it simple — the cost is one API call.
    """
    try:
        resp = ecr().get_authorization_token()
        data = resp["authorizationData"][0]
        token = base64.b64decode(data["authorizationToken"]).decode("utf-8")
        username, password = token.split(":", 1)
        return username, password, data["proxyEndpoint"]
    except ClientError as e:
        raise ECRError(f"failed to get ECR auth token: {e}") from e


def delete_repository(slug: str) -> None:
    """Best-effort delete. Used during deployment teardown.

    We force=True so non-empty repos are deleted along with their images.
    Missing repos are not an error — the caller doesn't always know whether
    we got far enough to create one.
    """
    name = _settings.ecr_repo_name(slug)
    try:
        ecr().delete_repository(repositoryName=name, force=True)
        log.info("ecr.repo_deleted", name=name)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "RepositoryNotFoundException":
            log.info("ecr.repo_already_gone", name=name)
            return
        # Don't raise on teardown — log and keep going so we attempt to clean
        # up other resources too.
        log.warning("ecr.repo_delete_failed", name=name, error=str(e))
