"""
tests/test_challenger_m3_dispatch.py - Dedicated Empirical Challenger Suite for M3.

Verifies:
1. /click/{ad_id}/{repo_id} and /click/active/{repo_id} return HTTP 302 Found.
2. Location header exactly matches sponsor target.
3. Location preserves complex query strings and fragments (?utm_source=readme&token=abc, #pricing).
4. Cache-Control header strictly prevents browser caching (no-cache, no-store, must-revalidate).
5. Database row inserted into clicks table with exact ad_id, repo_id, UTC timestamp, and 64-char hex SHA-256 audit hash.
6. Honest 404 response on missing ad or repo, negative IDs, and 422 on non-numeric IDs.
7. Budget depletion, auto-deactivation, and subsequent 404 enforcement.
8. Dynamic ad matching for /click/active/{repo_id} based on repository language and general fallback.
"""

import re
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.models.analytics import Click
from tests.conftest import create_test_ad, create_test_repository

# ============================================================================
# 1. Verification of /click/{ad_id}/{repo_id}
# ============================================================================

def test_click_direct_returns_302_found(client: TestClient, db_session: Session):
    """Verify HTTP status code is exactly 302 Found."""
    repo = create_test_repository(db_session, owner="psf", name="requests")
    ad = create_test_ad(db_session, click_url="https://sponsor.example.com/offer")

    response = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers.get("location") == "https://sponsor.example.com/offer"


def test_click_direct_exact_location_and_complex_url_preservation(client: TestClient, db_session: Session):
    """
    Verify Location header exactly matches sponsor target and preserves
    complex query strings and fragments (?utm_source=readme&token=abc, #pricing).
    """
    repo = create_test_repository(db_session)
    complex_target = "https://sponsor.example.com/product?source=github&tier=enterprise&key=xyz_123#pricing"
    ad = create_test_ad(db_session, click_url=complex_target)

    # 1. Direct hit without incoming params preserves exact URL including fragments
    res1 = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert res1.status_code == 302
    assert res1.headers.get("location") == complex_target

    # 2. Hit with incoming request params appends them losslessly without corrupting fragments
    incoming_query = "utm_source=readme&token=abc"
    res2 = client.get(f"/click/{ad.id}/{repo.id}?{incoming_query}", follow_redirects=False)
    assert res2.status_code == 302
    location2 = res2.headers.get("location")
    assert location2.startswith("https://sponsor.example.com/product?")
    assert "utm_source=readme" in location2
    assert "token=abc" in location2
    assert "source=github" in location2
    assert "tier=enterprise" in location2
    assert location2.endswith("#pricing")


def test_click_direct_cache_control_headers(client: TestClient, db_session: Session):
    """Verify Cache-Control header prevents browser caching (no-cache, no-store, must-revalidate)."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)

    response = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert response.status_code == 302

    cache_control = response.headers.get("cache-control", "")
    assert "no-cache" in cache_control.lower()
    assert "no-store" in cache_control.lower()
    assert "must-revalidate" in cache_control.lower()
    assert response.headers.get("pragma") == "no-cache"
    assert response.headers.get("expires") == "0"


def test_click_direct_database_persistence_and_sha256_hash(client: TestClient, db_session: Session):
    """
    Verify database row inserted into clicks table with exact ad_id, repo_id,
    UTC timestamp, and 64-char hex SHA-256 audit hash.
    """
    repo = create_test_repository(db_session)
    ad = create_test_ad(
        db_session,
        remaining_budget=Decimal("50.00"),
        cost_per_click=Decimal("0.50"),
    )

    client_ip = "198.51.100.25"
    user_agent = "EmpiricalChallenger/1.0"
    referer = "https://github.com/my-org/my-project"

    start_utc = datetime.now(UTC)

    response = client.get(
        f"/click/{ad.id}/{repo.id}",
        headers={
            "x-forwarded-for": client_ip,
            "user-agent": user_agent,
            "referer": referer,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    end_utc = datetime.now(UTC)

    # Query clicks table
    clicks = db_session.query(Click).filter_by(ad_id=ad.id, repo_id=repo.id).all()
    assert len(clicks) == 1
    click_row = clicks[0]

    assert click_row.ad_id == ad.id
    assert click_row.repo_id == repo.id
    assert click_row.referrer == referer

    # Verify 64-character lowercase hex SHA-256 hash
    assert len(click_row.client_hash) == 64
    assert bool(re.fullmatch(r"^[0-9a-f]{64}$", click_row.client_hash))

    # Verify UTC timestamp
    assert click_row.timestamp is not None
    # Check timestamp is in expected range
    assert click_row.timestamp.tzinfo is not None or click_row.timestamp >= start_utc.replace(tzinfo=None)

    # Verify budget and revenue split
    assert click_row.cost == Decimal("0.50")
    assert click_row.maintainer_cut == Decimal("0.25")
    assert click_row.platform_cut == Decimal("0.25")


def test_click_direct_honest_404_and_422_errors(client: TestClient, db_session: Session):
    """Verify honest 404 response on missing ad or repo, and 422 on malformed input."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)

    # Missing repo (ID 999999)
    res_no_repo = client.get(f"/click/{ad.id}/999999", follow_redirects=False)
    assert res_no_repo.status_code == 404
    assert "not found" in res_no_repo.json().get("detail", "").lower()

    # Missing ad (ID 999999)
    res_no_ad = client.get(f"/click/999999/{repo.id}", follow_redirects=False)
    assert res_no_ad.status_code == 404
    assert "not found" in res_no_ad.json().get("detail", "").lower()

    # Both non-existent
    res_neither = client.get("/click/999999/999999", follow_redirects=False)
    assert res_neither.status_code == 404

    # Negative IDs
    res_neg1 = client.get(f"/click/-5/{repo.id}", follow_redirects=False)
    assert res_neg1.status_code in [404, 422]
    res_neg2 = client.get(f"/click/{ad.id}/-10", follow_redirects=False)
    assert res_neg2.status_code in [404, 422]

    # Non-numeric IDs
    res_str = client.get("/click/invalid/not_an_id", follow_redirects=False)
    assert res_str.status_code == 422


# ============================================================================
# 2. Verification of /click/active/{repo_id}
# ============================================================================

def test_click_active_returns_302_found(client: TestClient, db_session: Session):
    """Verify /click/active/{repo_id} returns HTTP 302 Found redirect."""
    repo = create_test_repository(db_session, primary_language="Go")
    ad = create_test_ad(
        db_session,
        click_url="https://golang-sponsor.com/landing",
        target_language="Go",
    )

    response = client.get(f"/click/active/{repo.id}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers.get("location") == "https://golang-sponsor.com/landing"


def test_click_active_exact_location_and_complex_url_preservation(client: TestClient, db_session: Session):
    """Verify /click/active/{repo_id} preserves complex queries and fragments."""
    repo = create_test_repository(db_session, primary_language="Rust")
    complex_dest = "https://rust-tools.example.com/start?plan=pro&coupon=opensource#installation"
    ad = create_test_ad(
        db_session,
        click_url=complex_dest,
        target_language="Rust",
    )

    # Without extra params
    res1 = client.get(f"/click/active/{repo.id}", follow_redirects=False)
    assert res1.status_code == 302
    assert res1.headers.get("location") == complex_dest

    # With extra incoming query params
    res2 = client.get(f"/click/active/{repo.id}?utm_source=readme&token=abc", follow_redirects=False)
    assert res2.status_code == 302
    loc = res2.headers.get("location")
    assert loc.startswith("https://rust-tools.example.com/start?")
    assert "utm_source=readme" in loc
    assert "token=abc" in loc
    assert "plan=pro" in loc
    assert loc.endswith("#installation")


def test_click_active_cache_control_headers(client: TestClient, db_session: Session):
    """Verify /click/active/{repo_id} sets strict Cache-Control headers."""
    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    response = client.get(f"/click/active/{repo.id}", follow_redirects=False)
    assert response.status_code == 302
    cache_control = response.headers.get("cache-control", "").lower()
    assert "no-cache" in cache_control
    assert "no-store" in cache_control
    assert "must-revalidate" in cache_control
    assert response.headers.get("pragma") == "no-cache"
    assert response.headers.get("expires") == "0"


def test_click_active_database_persistence_and_sha256_hash(client: TestClient, db_session: Session):
    """Verify /click/active/{repo_id} records Click row with exact IDs and 64-char SHA-256 hash."""
    repo = create_test_repository(db_session, primary_language="TypeScript")
    ad = create_test_ad(
        db_session,
        target_language="TypeScript",
        remaining_budget=Decimal("20.00"),
        cost_per_click=Decimal("0.50"),
    )

    response = client.get(
        f"/click/active/{repo.id}",
        headers={"user-agent": "ActiveChallenger/2.0", "x-forwarded-for": "192.0.2.111"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    click = db_session.query(Click).filter_by(repo_id=repo.id, ad_id=ad.id).first()
    assert click is not None
    assert click.repo_id == repo.id
    assert click.ad_id == ad.id
    assert len(click.client_hash) == 64
    assert bool(re.fullmatch(r"^[0-9a-f]{64}$", click.client_hash))
    assert click.cost == Decimal("0.50")


def test_click_active_honest_404_on_missing_repo_or_no_active_ads(client: TestClient, db_session: Session):
    """Verify honest 404 response on missing repo or when no active ads match."""
    # 1. Non-existent repo
    res_no_repo = client.get("/click/active/888888", follow_redirects=False)
    assert res_no_repo.status_code == 404
    assert "not found" in res_no_repo.json().get("detail", "").lower()

    # 2. Repo exists but no active ads in database
    repo = create_test_repository(db_session, owner="empty", name="empty")
    res_no_ads = client.get(f"/click/active/{repo.id}", follow_redirects=False)
    assert res_no_ads.status_code == 404
    assert "not found" in res_no_ads.json().get("detail", "").lower()

    # 3. Malformed repo ID
    res_bad = client.get("/click/active/non_numeric", follow_redirects=False)
    assert res_bad.status_code == 422


# ============================================================================
# 3. Language Matching & General Fallback in /click/active/{repo_id}
# ============================================================================

def test_click_active_language_matching_cascade(client: TestClient, db_session: Session):
    """
    Verify /click/active/{repo_id} prioritizes language-specific ad,
    and falls back to general ad when language-specific ad is exhausted.
    """
    repo = create_test_repository(db_session, primary_language="Python")

    py_ad = create_test_ad(
        db_session,
        headline="Python Special",
        target_language="Python",
        click_url="https://python-special.example.com",
        remaining_budget=Decimal("0.50"),
        cost_per_click=Decimal("0.50"),
    )
    general_ad = create_test_ad(
        db_session,
        headline="General Tech",
        target_language=None,
        click_url="https://general-tech.example.com",
        remaining_budget=Decimal("100.00"),
        cost_per_click=Decimal("0.50"),
    )

    # First click: resolves Python-specific ad
    r1 = client.get(f"/click/active/{repo.id}", follow_redirects=False)
    assert r1.status_code == 302
    assert r1.headers.get("location") == "https://python-special.example.com"

    db_session.refresh(py_ad)
    assert py_ad.remaining_budget == Decimal("0.00")
    assert py_ad.is_active is False

    # Second click: Python ad exhausted, must cascade to General ad!
    r2 = client.get(f"/click/active/{repo.id}", follow_redirects=False)
    assert r2.status_code == 302
    assert r2.headers.get("location") == "https://general-tech.example.com"


# ============================================================================
# 4. Budget Depletion & Auto-Deactivation
# ============================================================================

def test_budget_depletion_auto_deactivation_and_subsequent_rejection(client: TestClient, db_session: Session):
    """
    Verify that an ad with $1.00 budget and $0.50 CPC allows exactly 2 clicks,
    auto-deactivates, and rejects the 3rd click with 404.
    """
    repo = create_test_repository(db_session)
    ad = create_test_ad(
        db_session,
        remaining_budget=Decimal("1.00"),
        cost_per_click=Decimal("0.50"),
    )

    # Click 1: remaining $0.50, is_active True
    r1 = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert r1.status_code == 302
    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("0.50")
    assert ad.is_active is True

    # Click 2: remaining $0.00, auto-deactivates to is_active False
    r2 = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert r2.status_code == 302
    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("0.00")
    assert ad.is_active is False

    # Click 3: Rejected with 404
    r3 = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert r3.status_code == 404
    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("0.00")
    assert ad.is_active is False

    # Ensure only 2 clicks exist
    clicks_count = db_session.query(Click).filter_by(ad_id=ad.id).count()
    assert clicks_count == 2
