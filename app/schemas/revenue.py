"""Pydantic v2 schemas for 50/50 Revenue Sharing and Ledger Reporting."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SplitTerms(BaseModel):
    maintainer_percentage: float = 50.0
    platform_percentage: float = 50.0


class Financials(BaseModel):
    currency: str = "USD"
    total_impressions: int
    total_clicks: int
    gross_revenue: Decimal
    maintainer_earnings: Decimal
    platform_fee: Decimal
    unpaid_balance: Decimal


class EventSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_type: str
    timestamp: datetime
    ad_id: int
    gross_cost: Decimal
    maintainer_cut: Decimal
    platform_cut: Decimal


class RevenueReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    repo_id: int
    owner: str
    name: str
    claimed: bool
    claimed_by: str | None = None
    payout_address: str | None = None
    split_terms: SplitTerms = Field(default_factory=SplitTerms)
    financials: Financials
    recent_events: list[EventSummary] = Field(default_factory=list)


class MaintainerRevenueSummary(BaseModel):
    maintainer_handle: str
    total_repositories: int
    total_impressions: int
    total_clicks: int
    gross_revenue: Decimal
    total_earnings: Decimal
    repositories: list[RevenueReport] = Field(default_factory=list)


class RevenueResponse(BaseModel):
    """Flat response schema matching E2E test assertions and DISPATCH.md."""
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    repo_id: int = Field(..., description="Repository database identifier")
    maintainer_earnings: float = Field(..., description="Maintainer revenue share (50%)")
    platform_cut: float = Field(..., description="Platform cut share (50%)")
    platform_fee: float = Field(..., description="Platform fee share (synonym for platform_cut)")
    gross_revenue: float = Field(..., description="Total gross revenue from clicks")
    total_clicks: int = Field(default=0, description="Total count of clicks recorded")
    clicks_count: int = Field(default=0, description="Synonym for total_clicks")
    total_impressions: int = Field(default=0, description="Total count of impressions recorded")
    impressions_count: int = Field(default=0, description="Synonym for total_impressions")
    owner: str | None = Field(default=None, description="Repository owner")
    name: str | None = Field(default=None, description="Repository name")
    claimed: bool | None = Field(default=None, description="Maintainer claim status")
    claimed_by: str | None = Field(default=None, description="Maintainer handle")
    payout_address: str | None = Field(default=None, description="Maintainer payout address")

    @model_validator(mode="before")
    @classmethod
    def sync_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Sync platform_cut and platform_fee
            p_cut = data.get("platform_cut")
            p_fee = data.get("platform_fee")
            if p_cut is not None and p_fee is None:
                data["platform_fee"] = p_cut
            elif p_fee is not None and p_cut is None:
                data["platform_cut"] = p_fee

            # Sync total_clicks and clicks_count
            t_clicks = data.get("total_clicks")
            c_count = data.get("clicks_count")
            if t_clicks is not None and c_count is None:
                data["clicks_count"] = t_clicks
            elif c_count is not None and t_clicks is None:
                data["total_clicks"] = c_count

            # Sync total_impressions and impressions_count
            t_imp = data.get("total_impressions")
            i_count = data.get("impressions_count")
            if t_imp is not None and i_count is None:
                data["impressions_count"] = t_imp
            elif i_count is not None and t_imp is None:
                data["total_impressions"] = i_count
        return data


# Alias for RevenueReportResponse
RevenueReportResponse = RevenueResponse


class MaintainerSummaryResponse(BaseModel):
    """Maintainer aggregate revenue report across all claimed repositories."""
    model_config = ConfigDict(from_attributes=True)

    maintainer_handle: str
    total_repositories: int
    total_impressions: int
    total_clicks: int
    gross_revenue: float
    total_earnings: float
    repositories: list[RevenueResponse] = Field(default_factory=list)

