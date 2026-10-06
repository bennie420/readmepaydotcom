"""
Revenue API Router (app/routers/revenue.py).

Provides:
- GET /revenue/{repo_id}: Primary revenue report endpoint.
- GET /api/revenue/{repo_id}: Alias endpoint.
- GET /api/repos/{owner}/{repo}/revenue: Owner/name lookup revenue endpoint.
- GET /revenue/maintainers/{maintainer_handle}: Maintainer aggregate earnings.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.revenue import MaintainerSummaryResponse, RevenueResponse
from app.services.revenue_service import (
    MAX_SQLITE_INT64,
    calculate_repository_revenue,
    calculate_repository_revenue_by_owner_name,
    get_maintainer_revenue_summary,
)

router = APIRouter(tags=["Revenue"])


@router.get(
    "/revenue/maintainers/{maintainer_handle}",
    response_model=MaintainerSummaryResponse,
    summary="Get maintainer aggregate revenue summary across all claimed repositories",
)
def get_maintainer_summary(
    maintainer_handle: str = Path(..., min_length=1, description="GitHub maintainer username"),
    db: Session = Depends(get_db),
):
    return get_maintainer_revenue_summary(db=db, maintainer_handle=maintainer_handle)


@router.get(
    "/revenue/{repo_id}",
    response_model=RevenueResponse,
    summary="Get repository revenue report and 50/50 ledger division",
)
def get_repository_revenue_report(
    repo_id: int = Path(..., ge=1, le=MAX_SQLITE_INT64, description="Repository inventory ID"),
    db: Session = Depends(get_db),
):
    return calculate_repository_revenue(db=db, repo_id=repo_id)


@router.get(
    "/api/revenue/{repo_id}",
    response_model=RevenueResponse,
    summary="Get repository revenue report (API alias)",
)
def get_repository_revenue_report_alias(
    repo_id: int = Path(..., ge=1, le=MAX_SQLITE_INT64, description="Repository inventory ID"),
    db: Session = Depends(get_db),
):
    return calculate_repository_revenue(db=db, repo_id=repo_id)


@router.get(
    "/api/repos/{owner}/{repo}/revenue",
    response_model=RevenueResponse,
    summary="Get repository revenue report by owner and repo name",
)
def get_repository_revenue_by_slug(
    owner: str = Path(..., min_length=1, description="Repository owner"),
    repo: str = Path(..., min_length=1, description="Repository name"),
    db: Session = Depends(get_db),
):
    return calculate_repository_revenue_by_owner_name(db=db, owner=owner, name=repo)
