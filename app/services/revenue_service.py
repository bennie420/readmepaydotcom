"""
Revenue Reporting & 50/50 Ledger Service (app/services/revenue_service.py).

Provides:
- Exact 50/50 Decimal revenue split calculations with penny conservation (ROUND_HALF_UP).
- Repository-level click and impression analytics aggregation.
- Escrow accounting for unclaimed repositories.
- Multi-repository isolation and maintainer aggregate summaries.
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.analytics import Click, Impression
from app.models.repository import Repository

logger = logging.getLogger("revenue_service")

MAX_SQLITE_INT64 = 9223372036854775807


def compute_click_split(cost: Decimal) -> tuple[Decimal, Decimal]:
    """
    Calculate exact 50/50 split with penny conservation (ROUND_HALF_UP).
    Maintainer receives the half-cent in odd-penny cases.
    """
    if cost <= Decimal("0.00"):
        return Decimal("0.00"), Decimal("0.00")

    half = cost / Decimal(2)
    if cost == cost.quantize(Decimal("0.01")):
        maintainer_cut = half.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        maintainer_cut = half
    platform_cut = cost - maintainer_cut
    return maintainer_cut, platform_cut


def calculate_repository_revenue(db: Session, repo_id: int) -> dict[str, Any]:
    """
    Aggregate all Click and Impression records for repo_id and calculate exact 50/50 financial ledger.

    Returns dict matching RevenueResponse schema.
    Raises HTTPException 404 if repository does not exist.
    """
    if repo_id <= 0 or repo_id > MAX_SQLITE_INT64:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with ID {repo_id} not found.",
        )

    repo = db.get(Repository, repo_id)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with ID {repo_id} not found.",
        )

    # 1. Impressions count
    impressions_count = (
        db.query(func.count(Impression.id))
        .filter(Impression.repo_id == repo_id)
        .scalar()
    ) or 0

    # 2. Clicks query and exact math calculation
    clicks = db.query(Click).filter(Click.repo_id == repo_id).all()
    clicks_count = len(clicks)

    gross_revenue = Decimal("0.00")
    for c in clicks:
        cost = Decimal(str(c.cost)) if c.cost is not None else Decimal("0.00")
        gross_revenue += cost

    if gross_revenue <= Decimal("0.00"):
        maintainer_earnings = Decimal("0.00")
        platform_cut = Decimal("0.00")
    else:
        half = gross_revenue / Decimal(2)
        if gross_revenue == gross_revenue.quantize(Decimal("0.01")):
            maintainer_earnings = half.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            maintainer_earnings = half
        platform_cut = gross_revenue - maintainer_earnings

    return {
        "repo_id": repo.id,
        "owner": repo.owner,
        "name": repo.name,
        "claimed": repo.claimed,
        "claimed_by": repo.maintainer_handle or repo.claimed_by,
        "payout_address": repo.payout_address,
        "maintainer_earnings": float(maintainer_earnings),
        "platform_cut": float(platform_cut),
        "platform_fee": float(platform_cut),
        "gross_revenue": float(gross_revenue),
        "total_clicks": clicks_count,
        "clicks_count": clicks_count,
        "total_impressions": impressions_count,
        "impressions_count": impressions_count,
    }


def calculate_repository_revenue_by_owner_name(
    db: Session, owner: str, name: str
) -> dict[str, Any]:
    """Look up repository by owner and name, then calculate revenue."""
    clean_owner = owner.strip()
    clean_name = name.strip()

    repo = (
        db.query(Repository)
        .filter(
            func.lower(Repository.owner) == clean_owner.lower(),
            func.lower(Repository.name) == clean_name.lower(),
        )
        .first()
    )
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository {clean_owner}/{clean_name} not found.",
        )
    return calculate_repository_revenue(db, repo.id)


def get_maintainer_revenue_summary(
    db: Session, maintainer_handle: str
) -> dict[str, Any]:
    """Calculate aggregate revenue across all claimed repositories for a maintainer."""
    clean_handle = maintainer_handle.strip()
    repos = (
        db.query(Repository)
        .filter(
            func.lower(Repository.claimed_by) == clean_handle.lower(),
            Repository.claimed == True,
        )
        .all()
    )

    total_impressions = 0
    total_clicks = 0
    total_gross = Decimal("0.00")
    total_earnings = Decimal("0.00")
    repo_reports = []

    for repo in repos:
        rep = calculate_repository_revenue(db, repo.id)
        repo_reports.append(rep)
        total_impressions += rep["total_impressions"]
        total_clicks += rep["total_clicks"]
        total_gross += Decimal(str(rep["gross_revenue"]))
        total_earnings += Decimal(str(rep["maintainer_earnings"]))

    return {
        "maintainer_handle": clean_handle,
        "total_repositories": len(repos),
        "total_impressions": total_impressions,
        "total_clicks": total_clicks,
        "gross_revenue": float(total_gross),
        "total_earnings": float(total_earnings),
        "repositories": repo_reports,
    }
