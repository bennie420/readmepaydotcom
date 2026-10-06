"""SQLAlchemy 2.0 Ad Campaign ORM Model."""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from app.database import Base

if TYPE_CHECKING:
    from app.models.analytics import Click, Impression


class Ad(Base):
    """Sponsor campaign, targeting criteria, and budget tracking."""
    __tablename__ = "ads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sponsor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    headline: Mapped[str] = mapped_column(String(255), nullable=False)
    call_to_action: Mapped[str] = mapped_column(String(100), nullable=False, default="Learn More")
    click_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    target_language: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    total_budget: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("100.00")
    )
    remaining_budget: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("100.00")
    )
    cost_per_click: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.50")
    )
    cost_per_impression: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("0.0020")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Aliases for cross-compatibility
    cta_text = synonym("call_to_action")
    active = synonym("is_active")
    cpc = synonym("cost_per_click")

    def __init__(self, **kwargs):
        if "cta_text" in kwargs and "call_to_action" not in kwargs:
            kwargs["call_to_action"] = kwargs.pop("cta_text")
        if "active" in kwargs and "is_active" not in kwargs:
            kwargs["is_active"] = kwargs.pop("active")
        if "cpc" in kwargs and "cost_per_click" not in kwargs:
            kwargs["cost_per_click"] = kwargs.pop("cpc")
        super().__init__(**kwargs)

    __table_args__ = (
        Index("ix_ad_match", "is_active", "target_language", "remaining_budget"),
    )

    # Relationships
    impressions: Mapped[list["Impression"]] = relationship(
        "Impression", back_populates="ad", cascade="all, delete-orphan"
    )
    clicks: Mapped[list["Click"]] = relationship(
        "Click", back_populates="ad", cascade="all, delete-orphan"
    )
