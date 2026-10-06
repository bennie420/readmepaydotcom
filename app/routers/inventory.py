"""
Inventory & Directory API Router (app/routers/inventory.py).

Provides:
- GET /api/inventory/repos: Catalog of registered repositories with stars, language, and claim status.
- GET /api/inventory/ads: Active sponsor campaigns with budgets, CPC, and targeting.
- GET /api/repos/{owner}/{repo}/preview: Live repository preview with matched ad and snippets.
"""

from __future__ import annotations

import logging
import urllib.parse
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.ad import Ad
from app.models.repository import Repository
from app.routers.maintainers import generate_badge_snippets, resolve_base_url
from app.services.github_service import get_or_fetch_repository
from app.services.matching_service import match_ad_for_repository

logger = logging.getLogger("router_inventory")

router = APIRouter(prefix="/api", tags=["Inventory"])


@router.get("/inventory/repos", summary="Get catalog of registered open-source repositories")
def get_repositories_inventory(
    q: Optional[str] = Query(None, description="Search term for owner or name"),
    language: Optional[str] = Query(None, description="Filter by primary programming language"),
    claimed: Optional[bool] = Query(None, description="Filter by claimed status"),
    limit: int = Query(50, ge=1, le=200, description="Max items to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    request: Request = None,
    db: Session = Depends(get_db),
):
    """Return paginated list of registered repositories in platform database."""
    base_url = resolve_base_url(request)
    query = db.query(Repository)

    if q:
        clean_q = q.strip().lower()
        query = query.filter(
            (func.lower(Repository.owner).contains(clean_q))
            | (func.lower(Repository.name).contains(clean_q))
            | (func.lower(Repository.description).contains(clean_q))
        )

    if language:
        query = query.filter(func.lower(Repository.primary_language) == language.strip().lower())

    if claimed is not None:
        query = query.filter(Repository.claimed == claimed)

    total = query.count()
    repos = query.order_by(desc(Repository.stars)).offset(offset).limit(limit).all()

    items = []
    for r in repos:
        items.append({
            "id": r.id,
            "owner": r.owner,
            "name": r.name,
            "full_name": f"{r.owner}/{r.name}",
            "description": r.description or "",
            "stars": r.stars,
            "primary_language": r.primary_language,
            "ci_status": r.ci_status,
            "claimed": r.claimed,
            "maintainer_handle": r.maintainer_handle or r.claimed_by,
            "claimed_at": r.claimed_at.isoformat() if r.claimed_at else None,
            "payout_address": r.payout_address,
            "badge_url": f"{base_url}/badge/{r.owner}/{r.name}.svg",
            "click_url": f"{base_url}/click/active/{r.id}",
            "revenue_url": f"{base_url}/revenue/{r.id}",
            "snippet_url": f"{base_url}/maintainers/snippet/{r.owner}/{r.name}",
        })

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "repositories": items,
    }


@router.get("/inventory/ads", summary="Get catalog of active sponsor campaigns")
def get_ads_inventory(
    target_language: Optional[str] = Query(None, description="Filter by targeted programming language"),
    active_only: bool = Query(True, description="Filter active campaigns only"),
    db: Session = Depends(get_db),
):
    """Return list of active sponsor campaigns and budget status."""
    query = db.query(Ad)

    if active_only:
        query = query.filter(Ad.is_active == True, Ad.remaining_budget > 0)

    if target_language:
        lang = target_language.strip().lower()
        query = query.filter(
            (func.lower(Ad.target_language) == lang)
            | (Ad.target_language == None)
            | (func.lower(Ad.target_language) == "general")
        )

    ads = query.order_by(desc(Ad.cpc)).all()

    items = []
    for a in ads:
        items.append({
            "id": a.id,
            "sponsor_name": a.sponsor_name,
            "headline": a.headline,
            "cta_text": a.cta_text,
            "click_url": a.click_url,
            "target_language": a.target_language or "general",
            "cpc": float(a.cpc),
            "remaining_budget": float(a.remaining_budget),
            "total_budget": float(a.total_budget) if getattr(a, "total_budget", None) is not None else float(a.remaining_budget),
            "is_active": a.is_active,
        })

    return {
        "total": len(items),
        "ads": items,
    }


class CreateCampaignRequest(BaseModel):
    sponsor_name: str = Field(..., min_length=2, max_length=255, description="Advertiser company or tool name")
    headline: str = Field(..., min_length=5, max_length=255, description="Short ad pitch (e.g. Stop debugging in production)")
    call_to_action: str = Field(default="Learn More", max_length=100, description="Button CTA (e.g. Try Free, Start Trial)")
    click_url: str = Field(..., min_length=10, max_length=1024, description="Destination landing page URL")
    target_language: str = Field(default="General", description="Programming language to target (Python, Rust, TypeScript, General)")
    initial_budget: Decimal = Field(default=Decimal("50.00"), ge=Decimal("5.00"), description="Initial deposit budget in USD")
    cost_per_click: Decimal = Field(default=Decimal("0.50"), ge=Decimal("0.10"), description="Bid per verified click in USD")


@router.post("/inventory/ads", summary="Create a new sponsor ad campaign")
def create_sponsor_campaign(
    payload: CreateCampaignRequest,
    db: Session = Depends(get_db),
):
    """Register a new sponsor ad campaign ready to receive funds via PayPal or Crypto."""
    lang = payload.target_language.strip()
    if lang.lower() in ("all", "general", "any"):
        lang = "General"

    new_ad = Ad(
        sponsor_name=payload.sponsor_name.strip(),
        headline=payload.headline.strip(),
        call_to_action=payload.call_to_action.strip(),
        click_url=payload.click_url.strip(),
        target_language=lang,
        total_budget=payload.initial_budget,
        remaining_budget=Decimal("0.00"),  # Funded upon checkout completion
        cost_per_click=payload.cost_per_click,
        cost_per_impression=Decimal("0.0020"),
        is_active=False,  # Activated upon payment capture
    )
    db.add(new_ad)
    db.commit()
    db.refresh(new_ad)

    return {
        "message": f"Campaign '{new_ad.sponsor_name}' created successfully. Proceed to checkout to fund your impression budget.",
        "ad_id": new_ad.id,
        "sponsor_name": new_ad.sponsor_name,
        "headline": new_ad.headline,
        "target_language": new_ad.target_language,
        "initial_budget": float(payload.initial_budget),
    }


@router.get("/repos/{owner}/{repo}/preview", summary="Get full live preview for a repository")
async def get_repository_preview(
    owner: str,
    repo: str,
    request: Request = None,
    db: Session = Depends(get_db),
):
    """Fetch or hydrate repository metadata and return live badge preview and snippets."""
    clean_owner = urllib.parse.unquote(owner).strip()
    clean_repo = urllib.parse.unquote(repo).strip()

    if not clean_owner or not clean_repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository owner and name are required.",
        )

    repository = await get_or_fetch_repository(db, clean_owner, clean_repo)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{clean_owner}/{clean_repo}' not found on GitHub.",
        )

    matched_ad = match_ad_for_repository(db, repository.primary_language, allow_platform_invite=False)
    base_url = resolve_base_url(request)
    snippets = generate_badge_snippets(repository.owner, repository.name, repository.id, base_url)

    matched_ad_dict = None
    if matched_ad is not None and getattr(matched_ad, "id", None) and matched_ad.id > 0:
        matched_ad_dict = {
            "id": matched_ad.id,
            "sponsor_name": matched_ad.sponsor_name,
            "headline": matched_ad.headline,
            "cta_text": matched_ad.cta_text,
            "click_url": matched_ad.click_url,
            "target_language": matched_ad.target_language,
            "cpc": float(matched_ad.cpc),
            "remaining_budget": float(matched_ad.remaining_budget),
        }

    return {
        "id": repository.id,
        "owner": repository.owner,
        "name": repository.name,
        "full_name": f"{repository.owner}/{repository.name}",
        "description": repository.description or "",
        "stars": repository.stars,
        "primary_language": repository.primary_language,
        "ci_status": repository.ci_status,
        "claimed": repository.claimed,
        "maintainer_handle": repository.maintainer_handle or repository.claimed_by,
        "claimed_at": repository.claimed_at.isoformat() if repository.claimed_at else None,
        "payout_address": repository.payout_address,
        "matched_ad": matched_ad_dict,
        "badge_url": f"{base_url}/badge/{repository.owner}/{repository.name}.svg",
        "click_url": f"{base_url}/click/active/{repository.id}",
        "snippets": snippets,
    }
