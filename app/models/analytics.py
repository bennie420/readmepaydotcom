"""SQLAlchemy 2.0 Impression and Click Analytics ORM Models."""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from app.database import Base

if TYPE_CHECKING:
    from app.models.ad import Ad
    from app.models.repository import Repository


class Impression(Base):
    """Badge impression record for analytics, deduplication, and CPM accounting."""
    __tablename__ = "impressions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ad_id: Mapped[int] = mapped_column(
        ForeignKey("ads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repo_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    cost: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("0.0020")
    )
    maintainer_share: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("0.0010")
    )
    platform_share: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("0.0010")
    )

    created_at = synonym("timestamp")

    def __init__(self, **kwargs):
        if "created_at" in kwargs and "timestamp" not in kwargs:
            kwargs["timestamp"] = kwargs.pop("created_at")
        super().__init__(**kwargs)

    __table_args__ = (
        Index("ix_imp_repo_time", "repo_id", "timestamp"),
        Index("ix_imp_dedup", "repo_id", "ad_id", "client_hash", "timestamp"),
    )

    # Relationships
    repository: Mapped["Repository"] = relationship("Repository", back_populates="impressions")
    ad: Mapped["Ad"] = relationship("Ad", back_populates="impressions")


class Click(Base):
    """Click redirect event, maintainer revenue allocation, and budget depletion."""
    __tablename__ = "clicks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ad_id: Mapped[int] = mapped_column(
        ForeignKey("ads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repo_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    cost: Mapped[Decimal] = mapped_column(
        Numeric(scale=None), nullable=False, default=Decimal("0.50")
    )
    maintainer_cut: Mapped[Decimal] = mapped_column(
        Numeric(scale=None), nullable=False, default=Decimal("0.25")
    )
    platform_cut: Mapped[Decimal] = mapped_column(
        Numeric(scale=None), nullable=False, default=Decimal("0.25")
    )
    referrer: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at = synonym("timestamp")
    cost_per_click = synonym("cost")

    def __init__(self, **kwargs):
        if "created_at" in kwargs and "timestamp" not in kwargs:
            kwargs["timestamp"] = kwargs.pop("created_at")
        if "cost_per_click" in kwargs and "cost" not in kwargs:
            kwargs["cost"] = kwargs.pop("cost_per_click")
        super().__init__(**kwargs)

    __table_args__ = (
        Index("ix_clicks_repo_time", "repo_id", "timestamp"),
        Index("ix_clicks_dedup", "repo_id", "ad_id", "client_hash", "timestamp"),
    )

    # Relationships
    repository: Mapped["Repository"] = relationship("Repository", back_populates="clicks")
    ad: Mapped["Ad"] = relationship("Ad", back_populates="clicks")
