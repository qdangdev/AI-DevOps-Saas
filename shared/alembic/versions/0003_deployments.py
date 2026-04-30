"""deployments table

Adds the `deployments` table that tracks one row per deploy attempt:
status, AI analysis result, image URI, AWS resource ARNs (for teardown),
and the live URL.

We use `native_enum=False` for the two enum columns so Postgres stores them
as VARCHAR + a CHECK constraint. That lets us add new values (e.g. a future
`paused` status) with an ALTER on the constraint instead of `ALTER TYPE` —
which on Postgres requires no-cache invalidation across long-lived
connections and has historically been a footgun for us.

Revision ID: 0003_deployments
Revises: 0002_password_auth
Create Date: 2026-04-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_deployments"
down_revision: Union[str, None] = "0002_password_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_STATUS_VALUES = ("pending", "analyzing", "building", "deploying", "running", "failed", "stopped")
_STEP_VALUES = ("analyze", "dockergen", "build", "deploy")


def upgrade() -> None:
    op.create_table(
        "deployments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("repo_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("commit_sha", sa.String(length=40), nullable=True),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("analysis", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("framework", sa.String(length=64), nullable=True),
        sa.Column("dockerfile_id", sa.String(length=512), nullable=True),
        sa.Column("image_uri", sa.String(length=1024), nullable=True),
        sa.Column("ecr_repository_arn", sa.String(length=512), nullable=True),
        sa.Column("task_definition_arn", sa.String(length=512), nullable=True),
        sa.Column("target_group_arn", sa.String(length=512), nullable=True),
        sa.Column("listener_rule_arn", sa.String(length=512), nullable=True),
        sa.Column("ecs_service_arn", sa.String(length=512), nullable=True),
        sa.Column("route53_record_name", sa.String(length=255), nullable=True),
        sa.Column("url", sa.String(length=512), nullable=True),
        sa.Column("failed_at_step", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["repo_id"], ["repos.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("slug", name="uq_deployments_slug"),
        sa.CheckConstraint(
            "status IN " + str(_STATUS_VALUES),
            name="ck_deployments_status",
        ),
        sa.CheckConstraint(
            "failed_at_step IS NULL OR failed_at_step IN " + str(_STEP_VALUES),
            name="ck_deployments_failed_at_step",
        ),
    )

    op.create_index("ix_deployments_repo_id", "deployments", ["repo_id"])
    op.create_index("ix_deployments_slug", "deployments", ["slug"])
    op.create_index("ix_deployments_status", "deployments", ["status"])
    op.create_index("ix_deployments_framework", "deployments", ["framework"])

    # GIN on the analysis JSONB so we can query by framework etc. cheaply.
    # Cheap enough on a low-write table; would reconsider if deployments
    # ever flipped to high-write.
    op.create_index(
        "ix_deployments_analysis_gin",
        "deployments",
        ["analysis"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_deployments_analysis_gin", table_name="deployments")
    op.drop_index("ix_deployments_framework", table_name="deployments")
    op.drop_index("ix_deployments_status", table_name="deployments")
    op.drop_index("ix_deployments_slug", table_name="deployments")
    op.drop_index("ix_deployments_repo_id", table_name="deployments")
    op.drop_table("deployments")
