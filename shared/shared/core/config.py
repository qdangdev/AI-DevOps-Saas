"""Application settings loaded from environment.

The *only* module that reads env. Both backend and worker import from here.
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    env: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- URLs ---
    backend_base_url: str = "http://localhost:8000"
    frontend_base_url: str = "http://localhost:5173"

    # --- Data ---
    database_url: PostgresDsn
    redis_url: RedisDsn

    # --- Auth / crypto ---
    jwt_secret: str
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 30
    encryption_key: str  # Fernet key, urlsafe base64 32 bytes

    # --- GitHub OAuth ---
    github_client_id: str
    github_client_secret: str
    github_oauth_scopes: str = "read:user user:email repo"

    # --- LLM ---
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-4-6"

    # --- AWS: account / region ---------------------------------------------
    # The deploy worker assumes default boto3 credentials (env vars, EC2/IRSA,
    # or ~/.aws/credentials). We don't accept an access key in config — too easy
    # to leak via logs.
    aws_region: str = "us-east-1"
    aws_account_id: str = ""  # 12-digit AWS account number; required in prod

    # --- AWS: ECR ----------------------------------------------------------
    # Per-deployment images live at <account>.dkr.ecr.<region>.amazonaws.com/<ecr_namespace>/<slug>:<tag>
    ecr_namespace: str = "ai-devops-saas/users"

    # --- AWS: ECS ----------------------------------------------------------
    # Pre-provisioned cluster + networking. Created once by Terraform; the
    # worker only adds task definitions + services to it.
    ecs_cluster: str = ""              # cluster name, e.g. "ai-devops-saas-prod"
    ecs_task_execution_role_arn: str = ""  # role ECS uses to pull from ECR + write logs
    ecs_task_role_arn: str = ""            # role the user's container runs as (no AWS perms by default)
    ecs_subnet_ids: list[str] = Field(default_factory=list)   # private subnets for awsvpc tasks
    ecs_security_group_ids: list[str] = Field(default_factory=list)  # SG that allows ALB → task on container port
    ecs_log_group: str = "/ecs/ai-devops-saas/users"  # CloudWatch log group; must exist already

    # Resource shape per user task. Conservative defaults — most demo apps
    # don't need more than 0.25 vCPU / 512 MB.
    ecs_task_cpu: str = "256"     # 0.25 vCPU
    ecs_task_memory: str = "512"  # MB

    # --- AWS: ALB ----------------------------------------------------------
    # We hang every deployment off one shared ALB. Per-deployment we add:
    #   - a target group (one per service)
    #   - a listener rule with host_header = <slug>.<apps_domain>
    alb_arn: str = ""             # full ALB ARN (worker doesn't need it but keep for symmetry)
    alb_https_listener_arn: str = ""  # HTTPS:443 listener ARN — the rules attach here
    alb_vpc_id: str = ""          # VPC that the ALB + target groups live in
    # Listener rule priorities are 1..50000. We allocate from the top half
    # to leave room for static rules at the bottom.
    alb_rule_priority_min: int = 10000
    alb_rule_priority_max: int = 49999

    # --- AWS: Route53 + DNS ------------------------------------------------
    apps_domain: str = "apps.example.com"  # final URL is https://<slug>.<apps_domain>
    hosted_zone_id: str = ""               # Route53 hosted zone for apps_domain
    # The ALB's DNS name + zone — used for ALIAS records pointing to it.
    alb_dns_name: str = ""
    alb_hosted_zone_id: str = ""

    # --- Worker tunables ---------------------------------------------------
    # How long deploy.run waits for ECS service stability + ALB health checks
    # before marking failed. Real Fargate cold starts are ~30s, but a misconfigured
    # health check can hang forever — we cap at 10 minutes.
    deploy_wait_timeout_seconds: int = 600
    deploy_wait_interval_seconds: int = 10

    @property
    def github_redirect_uri(self) -> str:
        return f"{self.backend_base_url}{self.api_v1_prefix}/auth/github/callback"

    @property
    def ecr_registry(self) -> str:
        """Host portion of the ECR registry: <account>.dkr.ecr.<region>.amazonaws.com."""
        return f"{self.aws_account_id}.dkr.ecr.{self.aws_region}.amazonaws.com"

    def ecr_repo_name(self, slug: str) -> str:
        """Per-deployment ECR repo: <namespace>/<slug>. ECR repo names allow / so we use it as a folder."""
        return f"{self.ecr_namespace}/{slug}"

    def ecr_image_uri(self, slug: str, tag: str) -> str:
        """Full image reference suitable for `docker push` / ECS task definition."""
        return f"{self.ecr_registry}/{self.ecr_repo_name(slug)}:{tag}"

    def deployment_url(self, slug: str) -> str:
        """The public URL the user gets back: https://<slug>.<apps_domain>."""
        return f"https://{slug}.{self.apps_domain}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
