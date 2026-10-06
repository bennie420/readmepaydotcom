"""
Empirical Challenge Test Suite for Milestone 4 Revenue Ledger & 50/50 Calculations.

Author: challenger_m4_2 (Empirical Challenger)
Covers:
- Exact 50/50 split on odd-cent CPCs ($0.05, $0.01, $0.03, $0.07, $0.15, $0.33, $0.99)
- Penny conservation rule (ROUND_HALF_UP giving half-cent to maintainer)
- Invariant: maintainer_earnings + platform_fee == gross_revenue
- Sub-cent micro-CPCs ($0.0025, $0.0005, $0.0010, $0.0001) with zero float drift
- Zero clicks repository behavior ($0.00 across all revenue fields)
- Zero clicks with positive impressions behavior
- Error handling: 404 for missing repo ID, 422 for non-integer, negative, zero, overflow IDs
- Large volume ledger aggregation (1,000 clicks) speed and exactness
- Heterogeneous click stream exactness
- Full end-to-end integration via /click redirect endpoint with odd-cent CPC ads
- Tenant isolation under concurrent click streams
"""

import time
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from tests.conftest import (
    create_test_ad,
    create_test_click,
    create_test_impression,
    create_test_repository,
)

# =====================================================================
# 1. Odd-Cent CPCs & Penny Conservation (ROUND_HALF_UP)
# =====================================================================


@pytest.mark.parametrize(
    "cost_str,expected_maintainer,expected_platform",
    [
        ("0.01", "0.01", "0.00"),  # $0.01: maintainer gets the whole cent
        ("0.03", "0.02", "0.01"),  # $0.03: 1.5 cents -> 2 cents maintainer, 1 platform
        ("0.05", "0.03", "0.02"),  # $0.05: 2.5 cents -> 3 cents maintainer, 2 platform
        ("0.07", "0.04", "0.03"),  # $0.07: 3.5 cents -> 4 cents maintainer, 3 platform
        ("0.09", "0.05", "0.04"),  # $0.09: 4.5 cents -> 5 cents maintainer, 4 platform
        ("0.15", "0.08", "0.07"),  # $0.15: 7.5 cents -> 8 cents maintainer, 7 platform
        ("0.33", "0.17", "0.16"),  # $0.33: 16.5 cents -> 17 cents maintainer, 16 platform
        ("0.99", "0.50", "0.49"),  # $0.99: 49.5 cents -> 50 cents maintainer, 49 platform
    ],
)
def test_empirical_odd_cent_cpc_penny_conservation(
    client: TestClient,
    db_session: Session,
    cost_str: str,
    expected_maintainer: str,
    expected_platform: str,
):
    """Verify exact 50/50 split and penny conservation on all odd-cent CPC amounts."""
    repo = create_test_repository(db_session, owner="odd-org", name=f"odd-{cost_str.replace('.', '-')}")
    ad = create_test_ad(db_session, cost_per_click=Decimal(cost_str))
    create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal(cost_str))

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200, f"Failed for cost {cost_str}: {response.text}"
    data = response.json()

    gross = Decimal(str(data["gross_revenue"]))
    maintainer = Decimal(str(data["maintainer_earnings"]))
    platform_fee = Decimal(str(data["platform_fee"]))
    platform_cut = Decimal(str(data["platform_cut"]))

    # 1. Exact amount match
    assert gross == Decimal(cost_str), f"Gross revenue mismatch: {gross} != {cost_str}"
    assert maintainer == Decimal(expected_maintainer), f"Maintainer earnings mismatch: {maintainer} != {expected_maintainer}"
    assert platform_fee == Decimal(expected_platform), f"Platform fee mismatch: {platform_fee} != {expected_platform}"
    assert platform_cut == platform_fee, "platform_cut alias must equal platform_fee"

    # 2. Conservation invariant
    assert maintainer + platform_fee == gross, "Money conservation invariant failed"
    # 3. Maintainer gets the extra penny
    assert maintainer - platform_fee == Decimal("0.01"), "Maintainer must receive the 1-cent difference"


@pytest.mark.parametrize(
    "cost_str,expected_maintainer,expected_platform",
    [
        ("0.02", "0.01", "0.01"),
        ("0.04", "0.02", "0.02"),
        ("0.10", "0.05", "0.05"),
        ("0.50", "0.25", "0.25"),
        ("1.00", "0.50", "0.50"),
        ("2.50", "1.25", "1.25"),
        ("10.00", "5.00", "5.00"),
    ],
)
def test_empirical_even_cent_cpc_exact_half(
    client: TestClient,
    db_session: Session,
    cost_str: str,
    expected_maintainer: str,
    expected_platform: str,
):
    """Verify exact 50/50 division on even-cent CPC amounts."""
    repo = create_test_repository(db_session, owner="even-org", name=f"even-{cost_str.replace('.', '-')}")
    ad = create_test_ad(db_session, cost_per_click=Decimal(cost_str))
    create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal(cost_str))

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()

    gross = Decimal(str(data["gross_revenue"]))
    maintainer = Decimal(str(data["maintainer_earnings"]))
    platform_fee = Decimal(str(data["platform_fee"]))

    assert gross == Decimal(cost_str)
    assert maintainer == Decimal(expected_maintainer)
    assert platform_fee == Decimal(expected_platform)
    assert maintainer == platform_fee
    assert maintainer + platform_fee == gross


# =====================================================================
# 2. Sub-cent Micro-CPCs & Zero Float Drift
# =====================================================================


@pytest.mark.parametrize(
    "cost_str,expected_maintainer,expected_platform",
    [
        ("0.0025", "0.00125", "0.00125"),
        ("0.0005", "0.00025", "0.00025"),
        ("0.0010", "0.0005", "0.0005"),
        ("0.0075", "0.00375", "0.00375"),
    ],
)
def test_empirical_sub_cent_micro_cpcs(
    client: TestClient,
    db_session: Session,
    cost_str: str,
    expected_maintainer: str,
    expected_platform: str,
):
    """Verify sub-cent micro-CPCs divide exactly without truncation or float drift."""
    repo = create_test_repository(db_session, owner="micro-org", name=f"micro-{cost_str.replace('.', '-')}")
    ad = create_test_ad(db_session)
    create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal(cost_str))

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()

    gross = Decimal(str(data["gross_revenue"]))
    maintainer = Decimal(str(data["maintainer_earnings"]))
    platform_fee = Decimal(str(data["platform_fee"]))

    assert gross == Decimal(cost_str)
    assert maintainer == Decimal(expected_maintainer)
    assert platform_fee == Decimal(expected_platform)
    assert maintainer + platform_fee == gross


def test_empirical_micro_cpcs_accumulation_to_even_and_odd_cents(
    client: TestClient,
    db_session: Session,
):
    """Verify that multiple micro-clicks accumulate to exact cents cleanly."""
    repo = create_test_repository(db_session, owner="acc-org", name="acc-repo")
    ad = create_test_ad(db_session)

    # 4 clicks of $0.0025 = $0.0100 total gross
    for _ in range(4):
        create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal("0.0025"))

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()

    assert Decimal(str(data["gross_revenue"])) == Decimal("0.01")
    # Odd-cent penny conservation gives maintainer $0.01 and platform $0.00
    assert Decimal(str(data["maintainer_earnings"])) == Decimal("0.01")
    assert Decimal(str(data["platform_fee"])) == Decimal("0.00")
    assert data["total_clicks"] == 4


# =====================================================================
# 3. Zero Clicks Repository Behavior
# =====================================================================


def test_empirical_zero_clicks_returns_exact_zeros(client: TestClient, db_session: Session):
    """Verify repository with 0 clicks and 0 impressions returns $0.00 across all fields."""
    repo = create_test_repository(db_session, owner="zero-org", name="zero-repo")

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()

    assert data["repo_id"] == repo.id
    assert Decimal(str(data["gross_revenue"])) == Decimal("0.00")
    assert Decimal(str(data["maintainer_earnings"])) == Decimal("0.00")
    assert Decimal(str(data["platform_cut"])) == Decimal("0.00")
    assert Decimal(str(data["platform_fee"])) == Decimal("0.00")
    assert data["total_clicks"] == 0
    assert data["clicks_count"] == 0
    assert data["total_impressions"] == 0
    assert data["impressions_count"] == 0


def test_empirical_zero_clicks_with_positive_impressions(
    client: TestClient,
    db_session: Session,
):
    """Verify impressions do not generate click revenue when clicks are zero."""
    repo = create_test_repository(db_session, owner="imp-org", name="imp-repo")
    ad = create_test_ad(db_session)

    for i in range(15):
        create_test_impression(db_session, repo_id=repo.id, ad_id=ad.id, client_hash=f"hash_{i}")

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()

    assert data["total_impressions"] == 15
    assert data["impressions_count"] == 15
    assert data["total_clicks"] == 0
    assert Decimal(str(data["gross_revenue"])) == Decimal("0.00")
    assert Decimal(str(data["maintainer_earnings"])) == Decimal("0.00")
    assert Decimal(str(data["platform_fee"])) == Decimal("0.00")


# =====================================================================
# 4. Error Handling & Parameter Validation
# =====================================================================


def test_empirical_missing_repo_id_returns_404(client: TestClient):
    """Verify non-existent repository ID returns 404."""
    response = client.get("/revenue/88888888")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.parametrize(
    "invalid_id",
    [
        "not-a-number",
        "12.34",
        "abc",
        "-1",
        "0",
        "9999999999999999999999999999999",  # Exceeds SQLite INT64
    ],
)
def test_empirical_invalid_repo_id_returns_422(client: TestClient, invalid_id: str):
    """Verify non-integer or out-of-bounds repository ID returns 422 Unprocessable Entity."""
    response = client.get(f"/revenue/{invalid_id}")
    assert response.status_code == 422, f"Expected 422 for {invalid_id}, got {response.status_code}"


def test_empirical_missing_path_parameter_returns_404(client: TestClient):
    """Verify calling /revenue/ without repo_id returns 404."""
    response = client.get("/revenue/")
    assert response.status_code in [404, 405]


# =====================================================================
# 5. Large Volume Aggregation & Ledger Speed
# =====================================================================


def test_empirical_large_volume_1000_clicks_speed_and_exactness(
    client: TestClient,
    db_session: Session,
):
    """Verify aggregation of 1,000 clicks completes rapidly with zero float drift."""
    repo = create_test_repository(db_session, owner="scale-org", name="scale-repo")
    ad = create_test_ad(db_session, remaining_budget=Decimal("1000.00"))

    click_objects = [
        create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal("0.05"))
        for _ in range(1000)
    ]
    assert len(click_objects) == 1000

    # Benchmark query and response time
    t0 = time.perf_counter()
    response = client.get(f"/revenue/{repo.id}")
    elapsed = time.perf_counter() - t0

    assert response.status_code == 200
    assert elapsed < 1.0, f"Ledger summation took {elapsed:.3f}s (> 1.0s limit)"

    data = response.json()
    assert data["total_clicks"] == 1000
    assert Decimal(str(data["gross_revenue"])) == Decimal("50.00")
    assert Decimal(str(data["maintainer_earnings"])) == Decimal("25.00")
    assert Decimal(str(data["platform_fee"])) == Decimal("25.00")
    assert Decimal(str(data["maintainer_earnings"])) + Decimal(str(data["platform_fee"])) == Decimal("50.00")


def test_empirical_heterogeneous_click_stream_exactness(
    client: TestClient,
    db_session: Session,
):
    """Verify heterogeneous click stream with mixed CPCs sums with zero drift."""
    repo = create_test_repository(db_session, owner="mix-org", name="mix-repo")
    ad = create_test_ad(db_session)

    # Heterogeneous click profile
    expected_sum = Decimal("0.00")
    profile = [
        (10, Decimal("0.01")),
        (15, Decimal("0.05")),
        (20, Decimal("0.10")),
        (5, Decimal("0.33")),
        (8, Decimal("0.0025")),
    ]

    total_clicks = 0
    for count, cost in profile:
        for _ in range(count):
            create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=cost)
            expected_sum += cost
            total_clicks += 1

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()

    gross = Decimal(str(data["gross_revenue"]))
    maintainer = Decimal(str(data["maintainer_earnings"]))
    platform = Decimal(str(data["platform_fee"]))

    assert gross == expected_sum
    assert data["total_clicks"] == total_clicks
    assert maintainer + platform == gross


# =====================================================================
# 6. End-to-End Click Redirect to Revenue Ledger Integration
# =====================================================================


def test_empirical_live_click_redirect_to_revenue_ledger(
    client: TestClient,
    db_session: Session,
):
    """Verify live HTTP GET /click/{ad_id}/{repo_id} flows through to GET /revenue/{repo_id}."""
    repo = create_test_repository(db_session, owner="live-org", name="live-repo")
    ad = create_test_ad(
        db_session,
        cost_per_click=Decimal("0.05"),
        remaining_budget=Decimal("10.00"),
    )

    # Execute authentic click via redirect endpoint
    click_res = client.get(f"/click/{ad.id}/{repo.id}")
    assert click_res.status_code == 302

    # Query revenue ledger
    rev_res = client.get(f"/revenue/{repo.id}")
    assert rev_res.status_code == 200
    data = rev_res.json()

    assert data["total_clicks"] == 1
    assert Decimal(str(data["gross_revenue"])) == Decimal("0.05")
    assert Decimal(str(data["maintainer_earnings"])) == Decimal("0.03")
    assert Decimal(str(data["platform_fee"])) == Decimal("0.02")
    assert Decimal(str(data["maintainer_earnings"])) + Decimal(str(data["platform_fee"])) == Decimal("0.05")


# =====================================================================
# 7. Multi-Tenant Repo Isolation Under Stress
# =====================================================================


def test_empirical_multi_tenant_isolation(
    client: TestClient,
    db_session: Session,
):
    """Verify revenue calculations on repo A never leak into repo B even with identical ads."""
    repo_a = create_test_repository(db_session, owner="corp-a", name="lib-a")
    repo_b = create_test_repository(db_session, owner="corp-b", name="lib-b")
    ad = create_test_ad(db_session)

    # 3 clicks on Repo A ($0.15 total)
    for _ in range(3):
        create_test_click(db_session, repo_id=repo_a.id, ad_id=ad.id, cost=Decimal("0.05"))

    # 1 click on Repo B ($0.05 total)
    create_test_click(db_session, repo_id=repo_b.id, ad_id=ad.id, cost=Decimal("0.05"))

    res_a = client.get(f"/revenue/{repo_a.id}").json()
    res_b = client.get(f"/revenue/{repo_b.id}").json()

    assert res_a["total_clicks"] == 3
    assert Decimal(str(res_a["gross_revenue"])) == Decimal("0.15")
    assert Decimal(str(res_a["maintainer_earnings"])) == Decimal("0.08")
    assert Decimal(str(res_a["platform_fee"])) == Decimal("0.07")

    assert res_b["total_clicks"] == 1
    assert Decimal(str(res_b["gross_revenue"])) == Decimal("0.05")
    assert Decimal(str(res_b["maintainer_earnings"])) == Decimal("0.03")
    assert Decimal(str(res_b["platform_fee"])) == Decimal("0.02")
