"""Unit Tests for Ad Matching Engine (app/services/matching_service.py)."""

from decimal import Decimal

from sqlalchemy.orm import Session

from app.services.matching_service import (
    match_ad_for_repository,
)
from tests.conftest import create_test_ad


def test_tier1_exact_language_match(db_session: Session):
    """Tier 1: Matches language when primary_language matches target_language exactly."""
    ad = create_test_ad(db_session, sponsor_name="Sentry", target_language="Python")
    matched = match_ad_for_repository(db_session, primary_language="Python")
    assert matched is not None
    assert matched.id == ad.id
    assert matched.sponsor_name == "Sentry"


def test_tier1_case_insensitive_match(db_session: Session):
    """Tier 1: Matches language case-insensitively ('python', 'PYTHON', 'PyThOn')."""
    ad = create_test_ad(db_session, target_language="Python")
    for variant in ["python", "PYTHON", "PyThOn", "pYtHoN"]:
        matched = match_ad_for_repository(db_session, primary_language=variant)
        assert matched is not None
        assert matched.id == ad.id


def test_tier1_whitespace_trimming(db_session: Session):
    """Tier 1: Strips leading/trailing whitespace before matching."""
    ad = create_test_ad(db_session, target_language="Rust")
    matched = match_ad_for_repository(db_session, primary_language="   Rust  ")
    assert matched is not None
    assert matched.id == ad.id


def test_tier1_special_characters_language(db_session: Session):
    """Tier 1: Matches languages with symbols like C++, C#, Objective-C."""
    ad_cpp = create_test_ad(db_session, target_language="C++")
    matched = match_ad_for_repository(db_session, primary_language="c++")
    assert matched is not None
    assert matched.id == ad_cpp.id


def test_tier2_fallback_to_general_when_unmatched(db_session: Session):
    """Tier 2: Falls back to universal general ad when repo language has no targeted ad."""
    create_test_ad(db_session, sponsor_name="GoOnly", target_language="Go")
    general_ad = create_test_ad(db_session, sponsor_name="GlobalCloud", target_language=None)

    matched = match_ad_for_repository(db_session, primary_language="Ruby")
    assert matched is not None
    assert matched.id == general_ad.id
    assert matched.sponsor_name == "GlobalCloud"


def test_tier2_fallback_for_none_or_empty_language(db_session: Session):
    """Tier 2: Primary language None or empty string routes directly to general ad."""
    general_ad = create_test_ad(db_session, sponsor_name="GeneralHost", target_language=None)

    assert match_ad_for_repository(db_session, primary_language=None).id == general_ad.id
    assert match_ad_for_repository(db_session, primary_language="").id == general_ad.id
    assert match_ad_for_repository(db_session, primary_language="   ").id == general_ad.id


def test_tier2_general_keyword_language(db_session: Session):
    """Tier 2: Ad with target_language='general' also qualifies as universal fallback."""
    general_ad = create_test_ad(db_session, sponsor_name="GeneralKw", target_language="general")
    matched = match_ad_for_repository(db_session, primary_language="Haskell")
    assert matched is not None
    assert matched.id == general_ad.id


def test_inactive_ads_excluded(db_session: Session):
    """Filtering: Inactive ads (is_active=False) are bypassed in cascade."""
    create_test_ad(db_session, sponsor_name="InactivePython", target_language="Python", active=False)
    general_ad = create_test_ad(db_session, sponsor_name="ActiveGeneral", target_language=None, active=True)

    matched = match_ad_for_repository(db_session, primary_language="Python")
    assert matched is not None
    assert matched.id == general_ad.id


def test_zero_budget_ads_excluded(db_session: Session):
    """Filtering: Ads with remaining_budget=0.00 are skipped even if active."""
    create_test_ad(db_session, sponsor_name="ZeroBudgetPython", target_language="Python", remaining_budget=Decimal("0.00"), active=True)
    general_ad = create_test_ad(db_session, sponsor_name="FundedGeneral", target_language=None, remaining_budget=Decimal("50.00"))

    matched = match_ad_for_repository(db_session, primary_language="Python")
    assert matched is not None
    assert matched.id == general_ad.id


def test_fifo_ad_rotation_on_depletion(db_session: Session):
    """Rotation: First created ad served until budget exhausted, then rotates to second ad."""
    ad1 = create_test_ad(db_session, sponsor_name="AdOne", target_language="Python", remaining_budget=Decimal("1.00"))
    ad2 = create_test_ad(db_session, sponsor_name="AdTwo", target_language="Python", remaining_budget=Decimal("10.00"))

    # Initial query matches AdOne
    assert match_ad_for_repository(db_session, "Python").id == ad1.id

    # Deplete AdOne budget
    ad1.remaining_budget = Decimal("0.00")
    db_session.commit()

    # Next query rotates to AdTwo
    assert match_ad_for_repository(db_session, "Python").id == ad2.id


def test_tier3_platform_onboarding_invite_when_no_ads(db_session: Session):
    """Tier 3: Platform open onboarding invite returned when database has zero active ads."""
    matched = match_ad_for_repository(db_session, primary_language="Rust", allow_platform_invite=True)
    assert matched is not None
    assert matched.id == 0
    assert "Sponsor" in matched.headline
    assert matched.click_url == "/maintainers"
    assert matched.cost_per_click == Decimal("0.00")


def test_tier3_disabled_returns_none(db_session: Session):
    """Tier 3: When allow_platform_invite=False, returns None when no commercial ads exist."""
    matched = match_ad_for_repository(db_session, primary_language="Rust", allow_platform_invite=False)
    assert matched is None
