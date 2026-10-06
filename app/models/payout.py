"""SQLAlchemy 2.0 Payout and Settlement ORM Models."""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PayoutBatch(Base):
    """Batch disbursement event across maintainers."""
    __tablename__ = "payout_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_reference: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)  # "paypal", "crypto"
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="SUCCESS")  # "PENDING", "SUCCESS", "FAILED"
    total_amount_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    external_payout_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    items: Mapped[list["PayoutItem"]] = relationship(
        "PayoutItem", back_populates="batch", cascade="all, delete-orphan"
    )


class PayoutItem(Base):
    """Individual maintainer disbursement record inside a payout batch."""
    __tablename__ = "payout_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("payout_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    maintainer_handle: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    payout_address: Mapped[str] = mapped_column(String(255), nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="SUCCESS")
    transaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    batch: Mapped["PayoutBatch"] = relationship("PayoutBatch", back_populates="items")
