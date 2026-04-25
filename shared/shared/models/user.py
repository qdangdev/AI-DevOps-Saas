"""User model — one row per authenticated user.

A user can sign up via either auth method (or both, linked by email):

  - **Email + password**: `email` + `password_hash` are required;
    GitHub fields stay NULL until they connect their GitHub account.
  - **GitHub OAuth**: `github_id`, `github_login`, `github_access_token_enc`
    are required; `password_hash` stays NULL.

Both paths produce a row that satisfies the same `get_current_user`
dependency, so route handlers don't care which method was used.
"""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Email is the canonical identity — required and unique. Either auth path
    # (signup or GitHub) populates this.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)

    # Set only for password users. NULL for users who only auth via GitHub.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Set only for GitHub-linked users. Nullable so password-only signups can
    # exist before they connect a repo, and so we don't lose the row if a user
    # later disconnects GitHub.
    github_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True, nullable=True)
    github_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    github_access_token_enc: Mapped[str | None] = mapped_column(String, nullable=True)

    avatar_url: Mapped[str | None] = mapped_column(String(1024))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    repos: Mapped[list["Repo"]] = relationship(  # noqa: F821
        back_populates="owner",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email}>"
