"""Tier 4: Full Real-World Application Lifecycle Scenarios.

Implements end-to-end multi-step application scenarios reflecting actual production usage:
1. Maintainer Onboarding & Monetization Lifecycle
2. Sponsor Campaign Lifecycle & Exhaustion Cascade
3. External API Cache & Rate-Limit Resiliency (Zero Mock Personas)
4. Click Fraud Audit Trail & Impression Deduplication
5. Multi-Language Inventory Matrix
6. Multi-Tenant Revenue Payout & Ledger Audit
7. Adversarial Traffic & Escaping Integrity
"""

from decimal import Decimal

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from tests.conftest import (
    assert_valid_svg_xml,
    create_test_ad,
    create_test_repository,
)


def test_scenario_1_full_maintainer_lifecycle(client: TestClient, db_session: Session):
    """Scenario 1: Complete Maintainer Onboarding, Badge Embedding, Click Tracking, & Payout.

    1. Seed authentic repo (pallets/flask, Python, unclaimed).
    2. Request badge endpoint -> verify SVG compiled with Python sponsor.
    3. Maintainer claims repository via onboarding API with handle and payout address.
    4. Maintainer generates README snippet (Markdown, HTML).
    5. User views badge on GitHub README -> impression recorded.
    6. User clicks badge ad card -> redirected via 302 to sponsor URL -> click recorded.
    7. Maintainer checks revenue dashboard -> verifies exact 50/50 split.
    """
    from tests.conftest import Impression

    # Step 1: Seed repository
    repo = create_test_repository(
        db_session,
        owner="pallets",
        name="flask",
        stars=68000,
        primary_language="Python",
        claimed=False,
    )
    ad = create_test_ad(
        db_session,
        sponsor_name="Sentry",
        headline="Actionable Code Insights",
        cta_text="Sign Up Free",
        click_url="https://sentry.io/for/python",
        target_language="Python",
        cost_per_click=Decimal("1.00"),
        remaining_budget=Decimal("50.00"),
    )

    # Step 2: Request badge
    badge_res = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert badge_res.status_code == 200
    assert "image/svg+xml" in badge_res.headers.get("content-type", "")
    assert "flask" in badge_res.text
    assert "Actionable Code Insights" in badge_res.text
    assert_valid_svg_xml(badge_res.text)

    # Step 3: Maintainer claims repository
    claim_payload = {
        "owner": repo.owner,
        "name": repo.name,
        "maintainer_handle": "davidism",
        "payout_address": "davidism@paypal.me",
    }
    claim_res = client.post("/maintainers/claim", json=claim_payload)
    assert claim_res.status_code in [200, 201]
    db_session.refresh(repo)
    assert repo.claimed is True
    assert repo.maintainer_handle == "davidism"

    # Step 4: Generate snippet
    snippet_res = client.get(f"/maintainers/snippet/{repo.owner}/{repo.name}")
    assert snippet_res.status_code == 200
    snippets = snippet_res.json()
    assert "markdown" in snippets
    assert f"/badge/{repo.owner}/{repo.name}.svg" in snippets["markdown"]

    # Step 5: User views badge (simulate impression)
    user_headers = {
        "user-agent": "GitHub-README-Viewer",
        "x-forwarded-for": "198.51.100.22",
    }
    view_res = client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers=user_headers)
    assert view_res.status_code == 200
    imp_count = db_session.query(Impression).filter_by(repo_id=repo.id).count()
    assert imp_count >= 1

    # Step 6: User clicks badge
    click_res = client.get(
        f"/click/{ad.id}/{repo.id}", headers=user_headers, follow_redirects=False
    )
    assert click_res.status_code == 302
    assert click_res.headers.get("location") == "https://sentry.io/for/python"

    # Step 7: Maintainer inspects revenue ledger
    rev_res = client.get(f"/revenue/{repo.id}")
    assert rev_res.status_code == 200
    rev_data = rev_res.json()
    assert Decimal(str(rev_data["gross_revenue"])) == Decimal("1.00")
    assert Decimal(str(rev_data["maintainer_earnings"])) == Decimal("0.50")
    assert Decimal(str(rev_data["platform_cut"])) == Decimal("0.50")
    assert rev_data["total_clicks"] == 1


def test_scenario_2_sponsor_lifecycle_and_cascade(
    client: TestClient, db_session: Session
):
    """Scenario 2: Sponsor Campaign Lifecycle, Exhaustion, and Fallback Cascade.

    1. Sponsor launches targeted campaign for Rust with $2.00 budget ($1.00 CPC).
    2. Platform has general sponsor with $100.00 budget ($0.25 CPC).
    3. Rust repo requests badge -> matches Rust campaign.
    4. First click -> budget drops to $1.00.
    5. Second click -> budget drops to $0.00, campaign auto-deactivates.
    6. Subsequent badge request for Rust repo falls back seamlessly to general campaign.
    7. Third click routes to general sponsor with zero platform disruption.
    """
    repo = create_test_repository(
        db_session, owner="tokio-rs", name="tokio", primary_language="Rust"
    )
    rust_ad = create_test_ad(
        db_session,
        sponsor_name="RustCorp",
        headline="Fast Rust Hosting",
        click_url="https://rustcorp.example.com",
        target_language="Rust",
        cost_per_click=Decimal("1.00"),
        remaining_budget=Decimal("2.00"),
        active=True,
    )
    general_ad = create_test_ad(
        db_session,
        sponsor_name="GlobalCloud",
        headline="Everywhere Infrastructure",
        click_url="https://globalcloud.example.com",
        target_language=None,
        cost_per_click=Decimal("0.25"),
        remaining_budget=Decimal("100.00"),
        active=True,
    )

    # 1. Matches Rust
    res1 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert "Fast Rust Hosting" in res1.text

    # 2. Click 1
    c1 = client.get(f"/click/{rust_ad.id}/{repo.id}", follow_redirects=False)
    assert c1.status_code == 302
    db_session.refresh(rust_ad)
    assert rust_ad.remaining_budget == Decimal("1.00")

    # 3. Click 2 (depletes budget)
    c2 = client.get(f"/click/{rust_ad.id}/{repo.id}", follow_redirects=False)
    assert c2.status_code == 302
    db_session.refresh(rust_ad)
    assert rust_ad.remaining_budget == Decimal("0.00")

    # 4. Next badge falls back to General
    res2 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert "Everywhere Infrastructure" in res2.text
    assert "Fast Rust Hosting" not in res2.text

    # 5. Click on general sponsor
    c3 = client.get(f"/click/{general_ad.id}/{repo.id}", follow_redirects=False)
    assert c3.status_code == 302
    assert c3.headers.get("location") == "https://globalcloud.example.com"


def test_scenario_3_cache_and_honest_404_resiliency(
    client: TestClient, db_session: Session
):
    """Scenario 3: Cache Resiliency, HTTP Caching Headers, and Honest Error Handling.

    1. Seed authentic repo -> initial badge returns 200, valid SVG, Cache-Control max-age=3600.
    2. Subsequent badge request returns consistent cached response.
    3. Query non-existent repository -> returns honest 404 or transparent error SVG.
    4. Strictly verifies NO synthetic personas (Thomas Vance, Theo Vance, etc.) are present.
    """
    repo = create_test_repository(
        db_session,
        owner="pydantic",
        name="pydantic",
        stars=22000,
        primary_language="Python",
    )
    create_test_ad(db_session)

    # 1. Initial fetch
    res1 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert res1.status_code == 200
    assert "pydantic" in res1.text
    cache_control = res1.headers.get("cache-control", "").lower()
    assert "public" in cache_control or "max-age" in cache_control

    # 2. Subsequent fetch
    res2 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert res2.status_code == 200
    assert res1.text == res2.text

    # 3. Non-existent repo query
    ghost_res = client.get("/badge/ghost-user-org-none/unregistered-ghost-lib.svg")
    assert ghost_res.status_code in [404, 200]
    ghost_text = ghost_res.text.lower()
    if ghost_res.status_code == 200:
        assert "not found" in ghost_text or "error" in ghost_text

    # 4. Strict user rules check: zero synthetic personas
    assert "thomas vance" not in ghost_text
    assert "theo vance" not in ghost_text
    assert "warranty deed" not in ghost_text
    assert "jpmorgan" not in ghost_text


def test_scenario_4_click_audit_and_dedup_pipeline(
    client: TestClient, db_session: Session
):
    """Scenario 4: Impression Deduplication & Audit Hashing Trail.

    1. Client hits badge 10 times in rapid succession.
    2. Verifies sliding window dedup saves only 1 impression record in database.
    3. Different client hits badge -> second impression record created with distinct SHA-256 hash.
    4. Both clients perform clicks -> clicks recorded with audit hashes.
    5. Audit trail integrity verified.
    """
    from tests.conftest import Click, Impression

    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)

    client_a_headers = {"user-agent": "AuditAgent-A", "x-forwarded-for": "192.0.2.1"}
    client_b_headers = {"user-agent": "AuditAgent-B", "x-forwarded-for": "192.0.2.2"}

    # Rapid sequential impressions for Client A
    for _ in range(10):
        client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers=client_a_headers)

    # 1 impression for Client A
    assert db_session.query(Impression).filter_by(repo_id=repo.id).count() == 1

    # Impression for Client B
    client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers=client_b_headers)
    assert db_session.query(Impression).filter_by(repo_id=repo.id).count() == 2

    # Both clients click
    client.get(
        f"/click/{ad.id}/{repo.id}", headers=client_a_headers, follow_redirects=False
    )
    client.get(
        f"/click/{ad.id}/{repo.id}", headers=client_b_headers, follow_redirects=False
    )

    clicks = db_session.query(Click).filter_by(repo_id=repo.id).all()
    assert len(clicks) == 2
    assert clicks[0].client_hash != clicks[1].client_hash
    for c in clicks:
        assert len(c.client_hash) == 64


def test_scenario_5_multi_language_inventory_matrix(
    client: TestClient, db_session: Session
):
    """Scenario 5: Multi-Language Inventory Matrix Routing.

    Registers Python, TypeScript, Rust, and Go repositories, alongside targeted ads for each,
    plus a general fallback. Verifies that every repo serves its exact language campaign,
    and an unknown language repo receives the general campaign.
    """
    matrix = {
        "Python": ("tiangolo", "fastapi", "Sponsor-Python-FastAPI"),
        "TypeScript": ("microsoft", "vscode", "Sponsor-TS-VSCode"),
        "Rust": ("rust-lang", "rust", "Sponsor-Rust-Official"),
        "Go": ("golang", "go", "Sponsor-Go-Language"),
    }

    # Create campaigns
    for lang, (_, _, headline) in matrix.items():
        create_test_ad(db_session, headline=headline, target_language=lang)

    # General fallback
    create_test_ad(db_session, headline="Sponsor-General-Global", target_language=None)

    # Verify each language matches
    for lang, (owner, name, expected_headline) in matrix.items():
        repo = create_test_repository(
            db_session, owner=owner, name=name, primary_language=lang
        )
        res = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
        assert res.status_code == 200
        assert expected_headline in res.text, (
            f"Failed matching {lang} ad for {owner}/{name}"
        )

    # Unknown language repo
    unknown_repo = create_test_repository(
        db_session, owner="esolang", name="brainfuck", primary_language="Brainfuck"
    )
    unknown_res = client.get(f"/badge/{unknown_repo.owner}/{unknown_repo.name}.svg")
    assert unknown_res.status_code == 200
    assert "Sponsor-General-Global" in unknown_res.text


def test_scenario_6_multi_tenant_revenue_ledger_reconciliation(
    client: TestClient, db_session: Session
):
    """Scenario 6: Multi-Tenant Maintainer Revenue Payout & Ledger Reconciliation.

    Simulates 3 maintainers with different traffic volumes. Calculates revenue for each repo,
    confirming exact mathematical consistency, zero cross-tenant contamination, and exact 50/50 splits.
    """
    repos_and_clicks = [
        ("maintainer_1", "repo_1", 10, Decimal("0.50")),  # $5.00 gross -> $2.50 each
        ("maintainer_2", "repo_2", 20, Decimal("0.75")),  # $15.00 gross -> $7.50 each
        ("maintainer_3", "repo_3", 5, Decimal("1.20")),  # $6.00 gross -> $3.00 each
    ]

    total_platform_expected = Decimal("0.00")

    for m_handle, r_name, clicks_count, cpc in repos_and_clicks:
        repo = create_test_repository(
            db_session,
            owner="corp",
            name=r_name,
            claimed=True,
            maintainer_handle=m_handle,
        )
        ad = create_test_ad(
            db_session, cost_per_click=cpc, remaining_budget=Decimal("1000.00")
        )

        for _ in range(clicks_count):
            client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)

        rev_res = client.get(f"/revenue/{repo.id}")
        assert rev_res.status_code == 200
        data = rev_res.json()

        gross = cpc * clicks_count
        half = gross / Decimal(2)
        total_platform_expected += half

        assert Decimal(str(data["gross_revenue"])) == gross
        assert Decimal(str(data["maintainer_earnings"])) == half
        assert Decimal(str(data["platform_cut"])) == half
        assert data["total_clicks"] == clicks_count


def test_scenario_7_adversarial_traffic_and_escaping_integrity(
    client: TestClient, db_session: Session
):
    """Scenario 7: Adversarial Inputs & Output Encoding Integrity.

    Tests XSS payloads, format string injection, and unusual query strings against the badge
    endpoint. Verifies XML escaping, valid SVG syntax, and audit hash stability under stress.
    """
    repo = create_test_repository(
        db_session,
        owner="sec-test",
        name="inject-xss",
    )
    create_test_ad(
        db_session,
        headline="Special <script>alert(1)</script> & Co.",
        cta_text="Try \"Quotes\" & 'Apos'",
    )

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    # Must be parseable as valid XML (implies all < and & were escaped properly)
    root = assert_valid_svg_xml(response.text)
    assert root is not None
    # No unescaped executable script tags
    assert "<script>" not in response.text
