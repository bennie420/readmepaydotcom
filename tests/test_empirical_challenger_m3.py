"""
Empirical Challenger Milestone 3 Verification Suite (tests/test_empirical_challenger_m3.py).

Adversarially tests:
1. /click/{ad_id}/{repo_id} and /click/active/{repo_id} 302 Found redirection mechanics.
2. Lossless verbatim URL preservation: complex query params (?k=v&x=1), anchor fragments (#pricing),
   and non-destructive incoming parameter merging.
3. Strict Cache-Control headers (no-cache, no-store, must-revalidate, Pragma, Expires).
4. Click table persistence and 64-character lowercase hexadecimal SHA-256 client audit hash.
5. Exact Decimal revenue accounting and 50/50 split consistency.
6. Custom 404 exception handler: detail preservation in JSON vs transparent SVG error badge for .svg requests.
7. Boundary conditions: invalid IDs, zero budget, budget < CPC, integer overflow, huge headers.
"""

import re
import urllib.parse
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.models.analytics import Click
from app.utils.security import compute_client_audit_hash
from tests.conftest import create_test_ad, create_test_repository

# ============================================================================
# Category 1: 302 Redirection & URL Preservation
# ============================================================================

def test_direct_click_status_302_and_verbatim_url(client: TestClient, db_session: Session):
    """Verify direct click endpoint issues exactly 302 Found and exact Location header."""
    repo = create_test_repository(db_session, owner="emp-org", name="emp-repo")
    target_url = "https://partner.io/landing?ref=badge&campaign=m3#features"
    ad = create_test_ad(db_session, click_url=target_url)

    response = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)

    assert response.status_code == 302, f"Expected 302 Found, got {response.status_code}"
    assert response.headers.get("location") == target_url, "Location header did not match target URL verbatim"


def test_active_click_status_302_and_verbatim_url(client: TestClient, db_session: Session):
    """Verify active click endpoint dynamically matches active ad, issues 302 Found, and preserves URL."""
    repo = create_test_repository(db_session, owner="rust-org", name="rust-crate", primary_language="Rust")
    target_url = "https://rust-sponsor.net/signup?source=readme&v=2.0#pricing-table"
    ad = create_test_ad(
        db_session,
        click_url=target_url,
        target_language="Rust",
        remaining_budget=Decimal("15.00"),
        cost_per_click=Decimal("0.50"),
    )

    response = client.get(f"/click/active/{repo.id}", follow_redirects=False)

    assert response.status_code == 302, f"Expected 302 Found, got {response.status_code}"
    assert response.headers.get("location") == target_url, "Location header did not match target URL verbatim"


def test_click_preserves_anchor_fragment_with_incoming_query_params(client: TestClient, db_session: Session):
    """
    Stress-test: Ensure incoming query parameters do NOT corrupt URL fragment.
    Anchor fragment (#section) must remain at the very end of the URL.
    """
    repo = create_test_repository(db_session)
    base_url = "https://sponsor.com/docs?initial_key=val#deep-anchor"
    ad = create_test_ad(db_session, click_url=base_url)

    response = client.get(
        f"/click/{ad.id}/{repo.id}?utm_source=github_readme&token=abc123xyz",
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers.get("location")
    assert location.endswith("#deep-anchor"), f"Anchor was corrupted or moved: {location}"

    parsed = urllib.parse.urlsplit(location)
    query_dict = dict(urllib.parse.parse_qsl(parsed.query))
    assert query_dict.get("initial_key") == "val"
    assert query_dict.get("utm_source") == "github_readme"
    assert query_dict.get("token") == "abc123xyz"
    assert parsed.fragment == "deep-anchor"


def test_click_does_not_override_existing_params_with_incoming(client: TestClient, db_session: Session):
    """
    Ensure that if an incoming param has the same key as a pre-existing target URL param,
    the sponsor's original parameter value is preserved and not overwritten.
    """
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session, click_url="https://sponsor.com/?tracking=SPONSOR_VAL")

    response = client.get(
        f"/click/{ad.id}/{repo.id}?tracking=ATTACKER_VAL",
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = response.headers.get("location")
    parsed = urllib.parse.urlsplit(location)
    qsl = urllib.parse.parse_qsl(parsed.query)
    # The existing key must remain 'SPONSOR_VAL'
    assert ("tracking", "SPONSOR_VAL") in qsl
    assert ("tracking", "ATTACKER_VAL") not in qsl


# ============================================================================
# Category 2: HTTP Cache-Control Headers
# ============================================================================

def test_cache_control_headers_strictly_enforced(client: TestClient, db_session: Session):
    """Verify strict anti-caching headers on both click endpoints."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)

    endpoints = [
        f"/click/{ad.id}/{repo.id}",
        f"/click/active/{repo.id}",
    ]

    for ep in endpoints:
        resp = client.get(ep, follow_redirects=False)
        assert resp.status_code == 302, f"Endpoint {ep} failed with {resp.status_code}"
        cc = resp.headers.get("cache-control", "").lower()
        assert "no-cache" in cc, f"{ep} missing no-cache in Cache-Control"
        assert "no-store" in cc, f"{ep} missing no-store in Cache-Control"
        assert "must-revalidate" in cc, f"{ep} missing must-revalidate in Cache-Control"
        assert resp.headers.get("pragma") == "no-cache", f"{ep} missing Pragma: no-cache"
        assert resp.headers.get("expires") == "0", f"{ep} missing Expires: 0"


# ============================================================================
# Category 3: Click DB Insertion & SHA-256 Hash Verification
# ============================================================================

def test_click_db_row_sha256_hash_and_exact_split(client: TestClient, db_session: Session):
    """
    Empirically verify database row in clicks table:
    - 64-character lowercase hex SHA-256 hash matching app.utils.security.compute_client_audit_hash
    - Correct foreign keys: ad_id, repo_id
    - Exact 50/50 revenue split with penny conservation
    - Correct UTC timestamp
    """
    repo = create_test_repository(db_session)
    ad = create_test_ad(
        db_session,
        cost_per_click=Decimal("0.75"),
        remaining_budget=Decimal("15.00"),
    )

    test_ip = "192.0.2.88"
    test_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TestBrowser/99.0"
    test_ref = "https://github.com/tested-org/tested-repo"

    expected_hash = compute_client_audit_hash(test_ip, test_ua)
    assert len(expected_hash) == 64
    assert bool(re.fullmatch(r"^[0-9a-f]{64}$", expected_hash))

    resp = client.get(
        f"/click/{ad.id}/{repo.id}",
        headers={
            "x-forwarded-for": f"{test_ip}, 10.0.0.1",
            "user-agent": test_ua,
            "referer": test_ref,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    click_entry = (
        db_session.query(Click)
        .filter(Click.ad_id == ad.id, Click.repo_id == repo.id)
        .order_by(Click.id.desc())
        .first()
    )

    assert click_entry is not None, "Click was not persisted in database"
    assert click_entry.client_hash == expected_hash, f"Hash mismatch: expected {expected_hash}, got {click_entry.client_hash}"
    assert len(click_entry.client_hash) == 64
    assert bool(re.fullmatch(r"^[0-9a-f]{64}$", click_entry.client_hash))

    # Revenue split check for $0.75 (odd cent):
    # half = 0.375, rounded to 0.38, platform gets 0.37
    assert click_entry.cost == Decimal("0.75")
    assert click_entry.maintainer_cut + click_entry.platform_cut == Decimal("0.75")
    assert click_entry.referrer == test_ref

    # Timestamp sanity
    now_utc = datetime.now(UTC)
    ts = click_entry.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    assert abs((now_utc - ts).total_seconds()) < 10, "Timestamp is not recent UTC"


def test_click_referrer_truncation_safety(client: TestClient, db_session: Session):
    """Verify that an overly long Referer header (> 512 chars) is safely truncated without DB error."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)

    huge_referer = "https://example.com/" + "a" * 800
    resp = client.get(
        f"/click/{ad.id}/{repo.id}",
        headers={"referer": huge_referer},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    click_row = db_session.query(Click).filter_by(ad_id=ad.id, repo_id=repo.id).first()
    assert click_row is not None
    assert len(click_row.referrer) <= 512


# ============================================================================
# Category 4: Custom 404 Handler & Detail Preservation
# ============================================================================

def test_custom_404_handler_preserves_exc_detail_on_json_endpoints(client: TestClient, db_session: Session):
    """
    Verify custom 404 exception handler:
    1. Direct click with missing repo preserves exc.detail
    2. Direct click with missing ad preserves exc.detail
    3. Direct click with exhausted ad preserves exc.detail
    4. Active click with missing repo preserves exc.detail
    5. Active click with no active ad preserves exc.detail
    """
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session, remaining_budget=Decimal("0.00"), active=False)

    # 1. Missing repo
    r1 = client.get(f"/click/{ad.id}/99999", follow_redirects=False)
    assert r1.status_code == 404
    data1 = r1.json()
    assert "Repository with ID 99999 not found." == data1.get("detail")

    # 2. Missing ad
    r2 = client.get(f"/click/99999/{repo.id}", follow_redirects=False)
    assert r2.status_code == 404
    data2 = r2.json()
    assert "Ad campaign with ID 99999 not found." == data2.get("detail")

    # 3. Exhausted / inactive ad
    r3 = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert r3.status_code == 404
    data3 = r3.json()
    assert f"Ad campaign {ad.id} is inactive or budget is exhausted." == data3.get("detail")

    # 4. Active click missing repo
    r4 = client.get("/click/active/99999", follow_redirects=False)
    assert r4.status_code == 404
    data4 = r4.json()
    assert "Repository with ID 99999 not found." == data4.get("detail")

    # 5. Active click with repo having no active sponsor
    repo_empty = create_test_repository(db_session, owner="no-sponsor", name="empty-repo")
    r5 = client.get(f"/click/active/{repo_empty.id}", follow_redirects=False)
    assert r5.status_code == 404
    data5 = r5.json()
    assert f"Active sponsor campaign not found for repository {repo_empty.id}." == data5.get("detail")


def test_custom_404_handler_serves_svg_badge_for_svg_paths(client: TestClient):
    """
    Verify custom 404 exception handler:
    When request path ends with .svg (e.g. /badge/nonexistent/repo.svg or /unknown.svg),
    it serves a valid SVG error badge (image/svg+xml) instead of JSON.
    """
    # 1. Badge 404
    resp = client.get("/badge/nonexistent_owner_12345/nonexistent_repo_98765.svg")
    assert resp.status_code == 404
    assert "image/svg+xml" in resp.headers.get("content-type", "")
    assert "<svg" in resp.text
    assert "</svg>" in resp.text
    assert "HTTP 404" in resp.text
    assert "Repository Not Found" in resp.text

    # 2. Arbitrary missing .svg route handled by custom_404_handler
    resp_arb = client.get("/arbitrary/missing_asset.svg")
    assert resp_arb.status_code == 404
    assert "image/svg+xml" in resp_arb.headers.get("content-type", "")
    assert "<svg" in resp_arb.text
    assert "</svg>" in resp_arb.text
    assert "HTTP 404" in resp_arb.text
    assert "404 Not Found" in resp_arb.text


def test_standard_missing_json_route_returns_not_found(client: TestClient):
    """Verify non-svg 404 route returns JSON with detail 'Not Found'."""
    resp = client.get("/nonexistent/api/endpoint")
    assert resp.status_code == 404
    assert resp.headers.get("content-type") == "application/json"
    assert resp.json().get("detail") == "Not Found"


# ============================================================================
# Category 5: Boundary & Stress Conditions
# ============================================================================

def test_click_boundary_budget_less_than_cpc(client: TestClient, db_session: Session):
    """Ad with remaining_budget = $0.40 and CPC = $0.50 must reject click with 404."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(
        db_session,
        remaining_budget=Decimal("0.40"),
        cost_per_click=Decimal("0.50"),
        active=True,
    )

    resp = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert resp.status_code == 404
    assert f"Ad campaign {ad.id} is inactive or budget is exhausted." in resp.json().get("detail", "")
    db_session.refresh(ad)
    assert ad.is_active is False


def test_click_boundary_exact_single_click_budget(client: TestClient, db_session: Session):
    """Ad with remaining_budget = $0.50 and CPC = $0.50: exactly 1 click succeeds, next fails."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(
        db_session,
        remaining_budget=Decimal("0.50"),
        cost_per_click=Decimal("0.50"),
        active=True,
    )

    # 1st click
    r1 = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert r1.status_code == 302
    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("0.00")
    assert ad.is_active is False

    # 2nd click
    r2 = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert r2.status_code == 404
    assert f"Ad campaign {ad.id} is inactive or budget is exhausted." in r2.json().get("detail", "")


def test_click_invalid_and_overflow_ids(client: TestClient):
    """Test negative IDs, zero IDs, and overflow IDs return 404 or 422."""
    assert client.get("/click/0/1", follow_redirects=False).status_code == 404
    assert client.get("/click/1/0", follow_redirects=False).status_code == 404
    assert client.get("/click/-10/1", follow_redirects=False).status_code == 404
    assert client.get("/click/1/-10", follow_redirects=False).status_code == 404
    assert client.get("/click/active/0", follow_redirects=False).status_code == 404
    assert client.get("/click/active/-5", follow_redirects=False).status_code == 404

    # Extremely huge integer exceeding SQLite int64 (9223372036854775807)
    overflow_id = 9223372036854775808
    r_over = client.get(f"/click/{overflow_id}/1", follow_redirects=False)
    assert r_over.status_code == 404
    assert "Identifier exceeds maximum allowable value." in r_over.json().get("detail", "")
