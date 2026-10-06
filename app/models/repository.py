"""SQLAlchemy 2.0 Repository ORM Model."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from app.database import Base

if TYPE_CHECKING:
    from app.models.analytics import Click, Impression


class Repository(Base):
    """Repository inventory and maintainer claim status."""
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    github_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    stars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    primary_language: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    ci_status: Mapped[str | None] = mapped_column(String(50), nullable=True, default="passing")
    claimed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    claimed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payout_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Alias maintainer_handle to claimed_by for compatibility across API and tests
    maintainer_handle = synonym("claimed_by")

    def __init__(self, **kwargs):
        if "maintainer_handle" in kwargs and "claimed_by" not in kwargs:
            kwargs["claimed_by"] = kwargs.pop("maintainer_handle")
        super().__init__(**kwargs)

    __table_args__ = (
        UniqueConstraint("owner", "name", name="uq_repo_owner_name"),
        Index("ix_repo_owner_name", "owner", "name"),
        Index("ix_repo_lang_claimed", "primary_language", "claimed"),
    )

    # Relationships
    impressions: Mapped[list["Impression"]] = relationship(
        "Impression", back_populates="repository", cascade="all, delete-orphan"
    )
    clicks: Mapped[list["Click"]] = relationship(
        "Click", back_populates="repository", cascade="all, delete-orphan"
    )
