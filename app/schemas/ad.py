"""Pydantic v2 schemas for Sponsor Ad Campaigns."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class AdBase(BaseModel):
    sponsor_name: str
    headline: str
    call_to_action: str = "Learn More"
    click_url: str
    target_language: str | None = None
    total_budget: Decimal = Field(default=Decimal("100.00"), ge=0)
    cost_per_click: Decimal = Field(default=Decimal("0.50"), gt=0)
    cost_per_impression: Decimal = Field(default=Decimal("0.0020"), ge=0)


class AdCreate(AdBase):
    pass


class AdUpdate(BaseModel):
    headline: str | None = None
    call_to_action: str | None = None
    click_url: str | None = None
    target_language: str | None = None
    is_active: bool | None = None
    remaining_budget: Decimal | None = None
    cost_per_click: Decimal | None = None
    cost_per_impression: Decimal | None = None


class AdResponse(AdBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    remaining_budget: Decimal
    created_at: datetime
    updated_at: datetime
