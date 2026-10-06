"""Pydantic schemas for Advertiser Self-Serve Campaigns."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class CampaignCreate(BaseModel):
    """Payload for creating a new advertising campaign."""
    sponsor_name: str = Field(..., min_length=2, max_length=255, description="Advertiser/Sponsor brand name")
    headline: str = Field(..., min_length=3, max_length=255, description="Short compelling ad copy")
    call_to_action: str = Field(default="Learn More", min_length=2, max_length=100, description="Badge button CTA text")
    click_url: str = Field(..., description="Destination landing page URL")
    target_language: Optional[str] = Field(default=None, max_length=100, description="Target language (e.g. 'Python', 'Rust') or null for general")
    total_budget: Decimal = Field(default=Decimal("100.00"), ge=Decimal("5.00"), description="Total initial campaign budget in USD")
    cost_per_click: Decimal = Field(default=Decimal("0.50"), ge=Decimal("0.05"), le=Decimal("25.00"), description="Cost per click in USD")
    cost_per_impression: Decimal = Field(default=Decimal("0.0020"), ge=Decimal("0.0001"), le=Decimal("1.00"), description="Cost per impression in USD")
    is_active: bool = Field(default=True, description="Whether campaign is immediately active")

    @field_validator("click_url")
    @classmethod
    def validate_click_url(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("click_url must start with http:// or https://")
        return v

    @field_validator("target_language")
    @classmethod
    def normalize_language(cls, v: Optional[str]) -> Optional[str]:
        if v:
            v_clean = v.strip()
            return v_clean if v_clean.lower() != "general" else None
        return None


class CampaignUpdate(BaseModel):
    """Payload for updating an existing campaign."""
    sponsor_name: Optional[str] = Field(None, min_length=2, max_length=255)
    headline: Optional[str] = Field(None, min_length=3, max_length=255)
    call_to_action: Optional[str] = Field(None, min_length=2, max_length=100)
    click_url: Optional[str] = None
    target_language: Optional[str] = None
    cost_per_click: Optional[Decimal] = Field(None, ge=Decimal("0.05"), le=Decimal("25.00"))
    cost_per_impression: Optional[Decimal] = Field(None, ge=Decimal("0.0001"), le=Decimal("1.00"))
    is_active: Optional[bool] = None

    @field_validator("click_url")
    @classmethod
    def validate_click_url(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not (v.startswith("http://") or v.startswith("https://")):
                raise ValueError("click_url must start with http:// or https://")
        return v


class CampaignFundRequest(BaseModel):
    """Payload for adding budget to a campaign."""
    amount: Decimal = Field(..., ge=Decimal("5.00"), le=Decimal("50000.00"), description="Additional budget amount in USD")
    payment_method: Optional[str] = Field(default="stripe_checkout", description="Payment reference method")
    payment_reference: Optional[str] = Field(default=None, description="Payment receipt ID or tx hash")


class CampaignResponse(BaseModel):
    """Complete campaign details including performance analytics."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    sponsor_name: str
    headline: str
    call_to_action: str
    click_url: str
    target_language: Optional[str]
    is_active: bool
    total_budget: Decimal
    remaining_budget: Decimal
    cost_per_click: Decimal
    cost_per_impression: Decimal
    created_at: datetime
    updated_at: datetime

    # Performance stats
    total_clicks: int = 0
    total_impressions: int = 0
    spent_budget: Decimal = Decimal("0.00")
    click_through_rate: float = 0.0
