"""dockerfile_content column on deployments

Stores the generated (or repo-supplied) Dockerfile inline so the API can
return it for the deployment detail view without re-cloning the repo.
TEXT (not String) because Dockerfiles can grow past the 1024-char cap we
default to elsewhere — a multi-stage Rust template is ~2KB.

Revision ID: 0004_dockerfile_content
Revises: 0003_deployments
Create Date: 2026-04-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_dockerfile_content"
down_revision: Union[str, None] = "0003_deployments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "deployments",
        sa.Column("dockerfile_content", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deployments", "dockerfile_content")
