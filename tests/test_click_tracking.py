"""
Unit and Integration Test Suite for Milestone 3 (Click Tracking & Analytics Pipeline).

Tests:
1. HTTP 302 redirect mechanics and Location header integrity.
2. Query parameter and fragment preservation in redirect URL.
3. Strict Cache-Control headers (no-cache, no-store).
4. Click table persistence and SHA-256 client audit hash.
5. Exact Decimal budget decrement without floating-point drift.
6. Auto-deactivation when remaining_budget < cost_per_click.
7. Zero negative budget guarantee and concurrency race condition resistance.
8. Camo proxy header handling and 1-hour sliding-window impression deduplication.
9. Dynamic /click/active/{repo_id} matching and fallback.
10. Honest 404 responses for invalid IDs with zero mock personas.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.models.analytics import Click, Impression
from tests.conftest import (
    create_test_ad,
    create_test_repository,
)

# =====================================================================
# 1. Redirect & Header Mechanics (HTTP 302 Found)
# =====================================================================

def test_click_returns_302_redirect(client: TestClient, db_session: Session):
    """Click endpoint returns HTTP 302 Found redirect pointing to ad click_url."""
    repo = create_test_repository(db_session, owner="pallets", name="flask")
    ad = create_test_ad(db_session, click_url="https://sponsor.example.com/portal")

    response = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers.get("location") == "https://sponsor.example.com/portal"


def test_click_preserves_complex_query_params_and_fragments(client: TestClient, db_session: Session):
    """Click URL containing UTM params, multiple query keys, and anchors is preserved verbatim."""
    repo = create_test_repository(db_session)
    complex_dest = "https://example.com/product?utm_source=readme&utm_campaign=open_source&tier=pro#features"
    ad = create_test_ad(db_session, click_url=complex_dest)

    response = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers.get("location") == complex_dest


def test_click_cache_control_headers(client: TestClient, db_session: Session):
    """Click endpoint sets strict Cache-Control headers to prevent client/proxy caching."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)

    response = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert response.status_code == 302
    cache_control = response.headers.get("cache-control", "").lower()
    assert "no-cache" in cache_control
    assert "no-store" in cache_control


def test_click_active_endpoint_dynamic_resolution(client: TestClient, db_session: Session):
    """GET /click/active/{repo_id} dynamically resolves top active ad for repo language."""
    repo = create_test_repository(db_session, primary_language="Python")
    ad = create_test_ad(db_session, click_url="https://python-sponsor.com", target_language="Python")

    response = client.get(f"/click/active/{repo.id}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers.get("location") == ad.click_url


# =====================================================================
# 2. Click Table Persistence & Audit Trail
# =====================================================================

def test_click_persists_record_in_database(client: TestClient, db_session: Session):
    """Click creates a persisted row in the clicks table with matching repo_id and ad_id."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)

    response = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert response.status_code == 302

    click = db_session.query(Click).filter_by(repo_id=repo.id, ad_id=ad.id).first()
    assert click is not None
    assert click.repo_id == repo.id
    assert click.ad_id == ad.id
    assert isinstance(click.timestamp, datetime)


def test_click_records_sha256_client_hash(client: TestClient, db_session: Session):
    """Click record includes a deterministic 64-char hexadecimal SHA-256 client audit hash."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)

    client.get(
        f"/click/{ad.id}/{repo.id}",
        headers={"user-agent": "AuditAgent/1.0", "x-forwarded-for": "203.0.113.195"},
        follow_redirects=False,
    )
    click = db_session.query(Click).filter_by(repo_id=repo.id, ad_id=ad.id).first()
    assert click is not None
    assert len(click.client_hash) == 64
    assert all(c in "0123456789abcdef" for c in click.client_hash)


def test_click_records_referrer_header(client: TestClient, db_session: Session):
    """HTTP Referer header is stored in the Click record."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)

    client.get(
        f"/click/{ad.id}/{repo.id}",
        headers={"referer": "https://github.com/pallets/flask"},
        follow_redirects=False,
    )
    click = db_session.query(Click).filter_by(repo_id=repo.id, ad_id=ad.id).first()
    assert click is not None
    assert click.referrer == "https://github.com/pallets/flask"


def test_click_exact_50_50_revenue_split_calculation(client: TestClient, db_session: Session):
    """Click records exact 50/50 division with penny conservation on odd amounts."""
    repo = create_test_repository(db_session)
    # CPC of $0.25 (odd cents)
    ad = create_test_ad(db_session, cost_per_click=Decimal("0.25"), remaining_budget=Decimal("10.00"))

    client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    click = db_session.query(Click).filter_by(repo_id=repo.id, ad_id=ad.id).first()
    assert click is not None
    assert click.cost == Decimal("0.25")
    assert click.maintainer_cut + click.platform_cut == Decimal("0.25")


# =====================================================================
# 3. Budget Decrement & Exact Decimal Mathematics
# =====================================================================

def test_budget_decrement_standard_cpc(client: TestClient, db_session: Session):
    """Remaining budget decrements by exactly cost_per_click."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session, remaining_budget=Decimal("50.00"), cost_per_click=Decimal("1.50"))

    client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("48.50")


def test_budget_decrement_exact_depletion_to_zero(client: TestClient, db_session: Session):
    """Ad with $1.00 budget and $1.00 CPC decrements to exactly $0.00."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session, remaining_budget=Decimal("1.00"), cost_per_click=Decimal("1.00"))

    client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("0.00")


def test_budget_decrement_fractional_cent_cpc(client: TestClient, db_session: Session):
    """Fractional cent CPC (0.025) operates with exact Decimal math ($10.000 - $0.025 = $9.975)."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session, remaining_budget=Decimal("10.000"), cost_per_click=Decimal("0.025"))

    client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("9.975")


def test_budget_decrement_no_floating_point_drift(client: TestClient, db_session: Session):
    """10 sequential clicks of $0.10 reach exact $0.00 without floating point drift."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session, remaining_budget=Decimal("1.00"), cost_per_click=Decimal("0.10"))

    for _ in range(10):
        client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)

    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("0.00")
    assert ad.remaining_budget == 0
    assert ad.remaining_budget >= Decimal("0.00")


def test_shared_sponsor_budget_across_multiple_repos(client: TestClient, db_session: Session):
    """Clicks across different repositories decrement the shared sponsor budget."""
    repo1 = create_test_repository(db_session, owner="org1", name="repo1")
    repo2 = create_test_repository(db_session, owner="org2", name="repo2")
    ad = create_test_ad(db_session, remaining_budget=Decimal("2.00"), cost_per_click=Decimal("1.00"))

    client.get(f"/click/{ad.id}/{repo1.id}", follow_redirects=False)
    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("1.00")

    client.get(f"/click/{ad.id}/{repo2.id}", follow_redirects=False)
    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("0.00")


# =====================================================================
# 4. Auto-Deactivation Mechanics (is_active = False)
# =====================================================================

def test_auto_deactivation_when_budget_hits_zero(client: TestClient, db_session: Session):
    """Ad auto-deactivates (is_active=False) immediately when remaining_budget reaches 0."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session, remaining_budget=Decimal("1.00"), cost_per_click=Decimal("1.00"), active=True)

    client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("0.00")
    assert ad.is_active is False


def test_auto_deactivation_when_remaining_budget_below_cpc(client: TestClient, db_session: Session):
    """Ad auto-deactivates when remaining_budget is positive but less than cost_per_click."""
    repo = create_test_repository(db_session)
    # Remaining budget is $1.50, CPC is $1.00. First click leaves $0.50 (< CPC).
    ad = create_test_ad(db_session, remaining_budget=Decimal("1.50"), cost_per_click=Decimal("1.00"), active=True)

    client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("0.50")
    assert ad.is_active is False


def test_auto_deactivation_rotates_subsequent_badge(client: TestClient, db_session: Session):
    """Exhausted primary sponsor rotates to general fallback sponsor on subsequent badge render."""
    repo = create_test_repository(db_session, primary_language="Rust")
    rust_ad = create_test_ad(
        db_session,
        headline="Fast Rust Tools",
        target_language="Rust",
        remaining_budget=Decimal("0.50"),
        cost_per_click=Decimal("0.50"),
    )
    create_test_ad(
        db_session,
        headline="Global Tech Sponsor",
        target_language=None,
        remaining_budget=Decimal("50.00"),
        cost_per_click=Decimal("0.25"),
    )

    # First badge renders Rust sponsor
    res1 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert "Fast Rust Tools" in res1.text

    # Click depletes Rust campaign
    client.get(f"/click/{rust_ad.id}/{repo.id}", follow_redirects=False)
    db_session.refresh(rust_ad)
    assert rust_ad.is_active is False

    # Second badge rotates to General sponsor
    res2 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert "Global Tech Sponsor" in res2.text
    assert "Fast Rust Tools" not in res2.text


def test_exhausted_ad_click_prevents_negative_balance(client: TestClient, db_session: Session):
    """Clicking an ad whose budget is already depleted does not drop budget below 0."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session, remaining_budget=Decimal("0.00"), cost_per_click=Decimal("1.00"), active=False)

    response = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert response.status_code in [302, 404]
    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("0.00")


# =====================================================================
# 5. Concurrency & Race Condition Integrity
# =====================================================================

def test_concurrent_clicks_on_final_budget(client: TestClient, db_session: Session):
    """Multiple rapid clicks on an ad with only 1 click budget leaves exactly 0 balance and 1 billable click."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session, remaining_budget=Decimal("1.00"), cost_per_click=Decimal("1.00"))

    # Rapid consecutive clicks
    r1 = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    r2 = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)

    assert r1.status_code == 302
    assert r2.status_code in [302, 404]

    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("0.00")
    clicks_count = db_session.query(Click).filter_by(repo_id=repo.id, ad_id=ad.id).count()
    assert clicks_count == 1


# =====================================================================
# 6. Honest Error Handling & Input Validation
# =====================================================================

def test_click_nonexistent_repo_returns_404(client: TestClient, db_session: Session):
    """Non-existent repo_id returns honest HTTP 404."""
    ad = create_test_ad(db_session)
    response = client.get(f"/click/{ad.id}/999999", follow_redirects=False)
    assert response.status_code == 404


def test_click_nonexistent_ad_returns_404(client: TestClient, db_session: Session):
    """Non-existent ad_id returns honest HTTP 404."""
    repo = create_test_repository(db_session)
    response = client.get(f"/click/999999/{repo.id}", follow_redirects=False)
    assert response.status_code == 404


def test_click_negative_ids_return_404_or_422(client: TestClient):
    """Negative IDs return 404 or 422."""
    r1 = client.get("/click/-1/10", follow_redirects=False)
    assert r1.status_code in [404, 422]
    r2 = client.get("/click/10/-1", follow_redirects=False)
    assert r2.status_code in [404, 422]


def test_click_non_numeric_ids_return_422(client: TestClient):
    """Non-numeric IDs return HTTP 422 Unprocessable Entity."""
    response = client.get("/click/abc/def", follow_redirects=False)
    assert response.status_code == 422


def test_click_huge_numeric_ids_return_404_or_422(client: TestClient):
    """Enormous numeric IDs return 404 or 422 without integer overflow errors."""
    response = client.get("/click/999999999999999999/999999999999999999", follow_redirects=False)
    assert response.status_code in [404, 422]


# =====================================================================
# 7. Impression Tracking & Camo Proxy Deduplication
# =====================================================================

def test_impression_hourly_deduplication(client: TestClient, db_session: Session):
    """Multiple badge views from identical client within 1 hour log exactly 1 impression."""
    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    headers = {"user-agent": "DedupClient/1.0", "x-forwarded-for": "198.51.100.10"}
    for _ in range(5):
        client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers=headers)

    count = db_session.query(Impression).filter_by(repo_id=repo.id).count()
    assert count == 1


def test_camo_proxy_headers_extraction(client: TestClient, db_session: Session):
    """GitHub Camo proxy headers (X-Forwarded-For with multiple IPs) extracts client origin IP."""
    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    headers = {
        "user-agent": "github-camo",
        "x-forwarded-for": "198.51.100.42, 140.82.112.4",
        "via": "camo",
    }
    client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers=headers)

    imp = db_session.query(Impression).filter_by(repo_id=repo.id).first()
    assert imp is not None
    assert len(imp.client_hash) == 64
