"""password auth columns

Adds email/password as a first-class auth method:
  - password_hash (nullable — empty for GitHub-only users)
  - email becomes UNIQUE NOT NULL (was indexed but nullable)
  - github_id, github_login, github_access_token_enc become NULLABLE
    so password-only users can exist before linking GitHub

Revision ID: 0002_password_auth
Revises: 0001_init
Create Date: 2026-04-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_password_auth"
down_revision: Union[str, None] = "0001_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # password_hash — nullable; only password users have one
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))

    # email — fill any NULLs with a placeholder before tightening to NOT NULL
    # (in dev there's no real data; in prod this migration runs before any
    # password users exist so existing rows already have GitHub-derived emails).
    op.execute("UPDATE users SET email = github_login || '@github.placeholder' WHERE email IS NULL")
    op.alter_column("users", "email", existing_type=sa.String(length=320), nullable=False)
    op.drop_index("ix_users_email", table_name="users")
    op.create_unique_constraint("uq_users_email", "users", ["email"])
    op.create_index("ix_users_email", "users", ["email"])

    # GitHub fields — relax to NULLABLE
    op.alter_column("users", "github_id", existing_type=sa.BigInteger(), nullable=True)
    op.alter_column("users", "github_login", existing_type=sa.String(length=255), nullable=True)
    op.alter_column("users", "github_access_token_enc", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    op.alter_column("users", "github_access_token_enc", existing_type=sa.String(), nullable=False)
    op.alter_column("users", "github_login", existing_type=sa.String(length=255), nullable=False)
    op.alter_column("users", "github_id", existing_type=sa.BigInteger(), nullable=False)

    op.drop_index("ix_users_email", table_name="users")
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.alter_column("users", "email", existing_type=sa.String(length=320), nullable=True)
    op.create_index("ix_users_email", "users", ["email"])

    op.drop_column("users", "password_hash")
