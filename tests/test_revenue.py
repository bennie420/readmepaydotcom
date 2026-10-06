"""
Unit and Integration Tests for Revenue Reporting and 50/50 Ledger (Milestone 4).

Covers:
- GET /revenue/{repo_id}
- GET /api/revenue/{repo_id} (alias)
- GET /api/repos/{owner}/{repo}/revenue (slug lookup)
- GET /revenue/maintainers/{maintainer_handle} (aggregate summary)
- Exact Decimal mathematics & penny conservation (ROUND_HALF_UP)
- Impression and Click counts aggregation
- Unclaimed repository escrow accounting
- Multi-repo tenant isolation
"""

from decimal import Decimal

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from tests.conftest import (
    create_test_ad,
    create_test_click,
    create_test_impression,
    create_test_repository,
)


def test_revenue_exact_50_50_split_even_amount(client: TestClient, db_session: Session):
    """Verify $1.00 gross revenue divides into exact $0.50 maintainer, $0.50 platform."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)
    create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal("1.00"))

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    assert Decimal(str(data["gross_revenue"])) == Decimal("1.00")
    assert Decimal(str(data["maintainer_earnings"])) == Decimal("0.50")
    assert Decimal(str(data["platform_cut"])) == Decimal("0.50")
    assert Decimal(str(data["platform_fee"])) == Decimal("0.50")
    assert data["total_clicks"] == 1
    assert data["clicks_count"] == 1


def test_revenue_penny_conservation_one_cent(client: TestClient, db_session: Session):
    """Verify $0.01 split allocates $0.01 to maintainer and $0.00 to platform without money loss."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)
    create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal("0.01"))

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    maintainer = Decimal(str(data["maintainer_earnings"]))
    platform = Decimal(str(data["platform_cut"]))
    assert maintainer + platform == Decimal("0.01")
    assert maintainer == Decimal("0.01")
    assert platform == Decimal("0.00")


def test_revenue_penny_conservation_three_cents(client: TestClient, db_session: Session):
    """Verify $0.03 split allocates $0.02 to maintainer and $0.01 to platform (penny conservation)."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)
    create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal("0.03"))

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    maintainer = Decimal(str(data["maintainer_earnings"]))
    platform = Decimal(str(data["platform_cut"]))
    assert maintainer + platform == Decimal("0.03")
    assert maintainer == Decimal("0.02")
    assert platform == Decimal("0.01")


def test_revenue_penny_conservation_thirty_three_cents(client: TestClient, db_session: Session):
    """Verify $0.33 split allocates $0.17 to maintainer and $0.16 to platform."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)
    create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal("0.33"))

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    maintainer = Decimal(str(data["maintainer_earnings"]))
    platform = Decimal(str(data["platform_cut"]))
    assert maintainer + platform == Decimal("0.33")
    assert maintainer == Decimal("0.17")
    assert platform == Decimal("0.16")


def test_revenue_large_scale_amount(client: TestClient, db_session: Session):
    """Verify $1,000,000.00 revenue divides into exact $500,000.00 each."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)
    create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal("1000000.00"))

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    assert Decimal(str(data["maintainer_earnings"])) == Decimal("500000.00")
    assert Decimal(str(data["platform_cut"])) == Decimal("500000.00")
    assert Decimal(str(data["gross_revenue"])) == Decimal("1000000.00")


def test_revenue_many_micro_clicks_accumulation(client: TestClient, db_session: Session):
    """Verify 50 small clicks of $0.10 accumulate to exact $5.00 gross, $2.50 each."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)
    for _ in range(50):
        create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal("0.10"))

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    assert Decimal(str(data["gross_revenue"])) == Decimal("5.00")
    assert Decimal(str(data["maintainer_earnings"])) == Decimal("2.50")
    assert Decimal(str(data["platform_cut"])) == Decimal("2.50")
    assert data["total_clicks"] == 50


def test_revenue_zero_clicks_returns_zero(client: TestClient, db_session: Session):
    """Verify repository with zero clicks returns 0.00 for all monetary fields."""
    repo = create_test_repository(db_session)
    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    assert Decimal(str(data["gross_revenue"])) == Decimal("0.00")
    assert Decimal(str(data["maintainer_earnings"])) == Decimal("0.00")
    assert Decimal(str(data["platform_cut"])) == Decimal("0.00")
    assert data["total_clicks"] == 0
    assert data["total_impressions"] == 0


def test_revenue_aggregates_impressions_and_clicks(client: TestClient, db_session: Session):
    """Verify revenue report accurately aggregates impressions and clicks."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)
    for i in range(4):
        create_test_impression(db_session, repo_id=repo.id, ad_id=ad.id, client_hash=f"hash_{i}")
    for _ in range(2):
        create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal("0.50"))

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["total_impressions"] == 4
    assert data["impressions_count"] == 4
    assert data["total_clicks"] == 2
    assert data["clicks_count"] == 2
    assert Decimal(str(data["gross_revenue"])) == Decimal("1.00")


def test_revenue_nonexistent_repo_returns_404(client: TestClient):
    """Verify requesting revenue for non-existent repo_id returns 404."""
    response = client.get("/revenue/99999999")
    assert response.status_code == 404


def test_revenue_invalid_id_type_returns_422(client: TestClient):
    """Verify requesting revenue with string repo_id returns 422."""
    response = client.get("/revenue/not-an-int")
    assert response.status_code == 422


def test_revenue_negative_id_returns_422(client: TestClient):
    """Verify requesting revenue with negative repo_id returns 422."""
    response = client.get("/revenue/-5")
    assert response.status_code == 422


def test_revenue_unclaimed_repository_escrow(client: TestClient, db_session: Session):
    """Verify unclaimed repository accrues maintainer earnings in escrow."""
    repo = create_test_repository(db_session, claimed=False)
    ad = create_test_ad(db_session)
    create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal("2.00"))

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["claimed"] is False
    assert Decimal(str(data["maintainer_earnings"])) == Decimal("1.00")
    assert Decimal(str(data["gross_revenue"])) == Decimal("2.00")


def test_revenue_multi_campaign_consolidation(client: TestClient, db_session: Session):
    """Verify clicks from multiple ad campaigns consolidate accurately."""
    repo = create_test_repository(db_session)
    ad1 = create_test_ad(db_session, cost_per_click=Decimal("0.60"))
    ad2 = create_test_ad(db_session, cost_per_click=Decimal("0.90"))

    create_test_click(db_session, repo_id=repo.id, ad_id=ad1.id, cost=Decimal("0.60"))
    create_test_click(db_session, repo_id=repo.id, ad_id=ad2.id, cost=Decimal("0.90"))

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    assert Decimal(str(data["gross_revenue"])) == Decimal("1.50")
    assert Decimal(str(data["maintainer_earnings"])) == Decimal("0.75")
    assert Decimal(str(data["platform_cut"])) == Decimal("0.75")


def test_revenue_multi_repo_isolation(client: TestClient, db_session: Session):
    """Verify revenue on Repo A does not leak into Repo B."""
    repoA = create_test_repository(db_session, owner="ownerA", name="repoA")
    repoB = create_test_repository(db_session, owner="ownerB", name="repoB")
    ad = create_test_ad(db_session)

    create_test_click(db_session, repo_id=repoA.id, ad_id=ad.id, cost=Decimal("3.00"))
    create_test_click(db_session, repo_id=repoB.id, ad_id=ad.id, cost=Decimal("1.00"))

    resA = client.get(f"/revenue/{repoA.id}")
    assert Decimal(str(resA.json()["gross_revenue"])) == Decimal("3.00")
    assert Decimal(str(resA.json()["maintainer_earnings"])) == Decimal("1.50")

    resB = client.get(f"/revenue/{repoB.id}")
    assert Decimal(str(resB.json()["gross_revenue"])) == Decimal("1.00")
    assert Decimal(str(resB.json()["maintainer_earnings"])) == Decimal("0.50")


def test_revenue_alias_routes(client: TestClient, db_session: Session):
    """Verify alias /api/revenue/{repo_id} and /api/repos/{owner}/{repo}/revenue work identically."""
    repo = create_test_repository(db_session, owner="route-owner", name="route-repo")
    ad = create_test_ad(db_session)
    create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal("1.00"))

    # Alias 1: /api/revenue/{repo_id}
    res1 = client.get(f"/api/revenue/{repo.id}")
    assert res1.status_code == 200
    assert Decimal(str(res1.json()["gross_revenue"])) == Decimal("1.00")

    # Alias 2: /api/repos/{owner}/{repo}/revenue
    res2 = client.get(f"/api/repos/{repo.owner}/{repo.name}/revenue")
    assert res2.status_code == 200
    assert Decimal(str(res2.json()["gross_revenue"])) == Decimal("1.00")


def test_maintainer_revenue_summary_endpoint(client: TestClient, db_session: Session):
    """Verify /revenue/maintainers/{handle} aggregates earnings across claimed repositories."""
    handle = "alice_founder"
    repo1 = create_test_repository(db_session, owner="org", name="tool1", claimed=True, maintainer_handle=handle)
    repo2 = create_test_repository(db_session, owner="org", name="tool2", claimed=True, maintainer_handle=handle)
    repo_other = create_test_repository(db_session, owner="org", name="other", claimed=True, maintainer_handle="other_dev")

    ad = create_test_ad(db_session)
    create_test_click(db_session, repo_id=repo1.id, ad_id=ad.id, cost=Decimal("2.00"))
    create_test_click(db_session, repo_id=repo2.id, ad_id=ad.id, cost=Decimal("4.00"))
    create_test_click(db_session, repo_id=repo_other.id, ad_id=ad.id, cost=Decimal("10.00"))

    response = client.get(f"/revenue/maintainers/{handle}")
    assert response.status_code == 200
    data = response.json()
    assert data["maintainer_handle"] == handle
    assert data["total_repositories"] == 2
    assert Decimal(str(data["gross_revenue"])) == Decimal("6.00")
    assert Decimal(str(data["total_earnings"])) == Decimal("3.00")
    assert data["total_clicks"] == 2
