"""
Ad Matching Engine (app/services/matching_service.py).

Resolves sponsor campaigns for repositories based on a 3-tier cascade:
1. Tier 1: Targeted programming language match (case-insensitive, budget > 0).
2. Tier 2: Universal general fallback sponsor (target_language IS NULL or 'general', budget > 0).
3. Tier 3: Platform open onboarding invite (strictly zero synthetic commercial sponsors).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.ad import Ad


def get_platform_onboarding_ad() -> Ad:
    """
    Return an authentic platform open onboarding invite (Tier 3 fallback).

    Strictly adheres to Zero Fake Commercial Sponsors: represents the platform's
    honest invitation for new sponsors and maintainers to join.
    """
    return Ad(
        id=0,
        sponsor_name="Open Source Sponsorship",
        headline="Sponsor this open source repository",
        call_to_action="Become a Sponsor",
        click_url="/maintainers",
        target_language=None,
        is_active=True,
        total_budget=Decimal("999999.00"),
        remaining_budget=Decimal("999999.00"),
        cost_per_click=Decimal("0.00"),
        cost_per_impression=Decimal("0.0000"),
    )


def match_ad_for_repository(
    db: Session,
    primary_language: str | None = None,
    allow_platform_invite: bool = True,
) -> Ad | None:
    """
    Resolve the highest-priority active ad campaign for a given repository.

    Match Cascade:
      Tier 1: Case-insensitive language match with remaining_budget > 0.
      Tier 2: Universal general fallback (target_language IS NULL or 'general') with remaining_budget > 0.
      Tier 3: Platform open onboarding invite (zero fake commercial sponsors).

    Args:
        db: SQLAlchemy synchronous Session.
        primary_language: Repository's primary programming language (e.g. 'Python', 'Rust').
        allow_platform_invite: Whether to return Tier 3 platform invite when no commercial ad exists.

    Returns:
        Matched Ad instance, or None if no ad and platform invite disabled.
    """
    ad, _ = match_ad_cascade(db, primary_language, allow_platform_invite=allow_platform_invite)
    return ad


def match_ad_cascade(
    db: Session,
    primary_language: str | None = None,
    allow_platform_invite: bool = True,
) -> tuple[Ad | None, int]:
    """
    Resolve ad and return tuple of (ad_instance, match_tier).

    Returns:
        (Ad, 1) for Language match
        (Ad, 2) for General fallback
        (Ad, 3) for Platform onboarding invite
        (None, 0) if no match and platform invite disabled
    """
    # Clean language input: strip whitespace
    clean_lang = primary_language.strip().lower() if primary_language and primary_language.strip() else None

    # Tier 1: Case-insensitive target language match
    if clean_lang:
        stmt_t1 = (
            select(Ad)
            .where(
                Ad.is_active == True,
                Ad.remaining_budget > Decimal("0.00"),
                func.lower(Ad.target_language) == clean_lang,
            )
            .order_by(Ad.id.asc())
        )
        t1_ad = db.scalars(stmt_t1).first()
        if t1_ad is not None:
            return t1_ad, 1

    # Tier 2: Universal general fallback ad (target_language IS NULL or 'general')
    stmt_t2 = (
        select(Ad)
        .where(
            Ad.is_active == True,
            Ad.remaining_budget > Decimal("0.00"),
            or_(
                Ad.target_language.is_(None),
                func.lower(Ad.target_language) == "general",
            ),
        )
        .order_by(Ad.id.asc())
    )
    t2_ad = db.scalars(stmt_t2).first()
    if t2_ad is not None:
        return t2_ad, 2

    # Tier 3: Platform open onboarding invite
    if allow_platform_invite:
        return get_platform_onboarding_ad(), 3

    return None, 0
