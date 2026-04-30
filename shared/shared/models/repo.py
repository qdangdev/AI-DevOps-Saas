"""Repo model — a GitHub repo a user has connected to our system."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base


class Repo(Base):
    __tablename__ = "repos"
    __table_args__ = (
        UniqueConstraint("owner_id", "github_repo_id", name="uq_repo_owner_github_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    owner_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    github_repo_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    full_name: Mapped[str] = mapped_column(String(512), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False, default="main")
    private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    html_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    clone_url: Mapped[str] = mapped_column(String(1024), nullable=False)

    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    owner: Mapped["User"] = relationship(back_populates="repos")  # noqa: F821
    deployments: Mapped[list["Deployment"]] = relationship(  # noqa: F821
        back_populates="repo",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Repo id={self.id} {self.full_name}>"
