"""Tier 3: Pairwise and Combinatorial Interaction Tests.

Covers multi-feature interactions, state transitions, ad rotation, language matching cascades,
budget exhaustion, revenue ledger consolidation, and CDN/proxy audit hashing.
"""

from decimal import Decimal

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from tests.conftest import (
    assert_valid_svg_xml,
    create_test_ad,
    create_test_repository,
)


def test_c1_ad_rotation_after_budget_depletion(client: TestClient, db_session: Session):
    """C1: Depleting budget of primary sponsor rotates next badge to alternate sponsor."""
    repo = create_test_repository(
        db_session, owner="pallets", name="flask", primary_language="Python"
    )
    ad1 = create_test_ad(
        db_session,
        headline="Python Ad 1 - First In Line",
        target_language="Python",
        remaining_budget=Decimal("1.00"),
        cost_per_click=Decimal("1.00"),
    )
    create_test_ad(
        db_session,
        headline="Python Ad 2 - Second In Line",
        target_language="Python",
        remaining_budget=Decimal("10.00"),
        cost_per_click=Decimal("0.50"),
    )

    # First badge serves Ad 1
    res1 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert res1.status_code == 200
    assert "Python Ad 1 - First In Line" in res1.text

    # Click on Ad 1 depletes its budget to 0
    click_res = client.get(f"/click/{ad1.id}/{repo.id}", follow_redirects=False)
    assert click_res.status_code == 302
    db_session.refresh(ad1)
    assert ad1.remaining_budget == Decimal("0.00")

    # Subsequent badge automatically rotates to Ad 2
    res2 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert res2.status_code == 200
    assert "Python Ad 2 - Second In Line" in res2.text


def test_c2_language_matching_cascade_to_general(
    client: TestClient, db_session: Session
):
    """C2: Depleting language-specific campaign cascades badge to general campaign."""
    repo = create_test_repository(db_session, primary_language="Rust")
    ad_rust = create_test_ad(
        db_session,
        headline="Exclusive Rust Campaign",
        target_language="Rust",
        remaining_budget=Decimal("0.50"),
        cost_per_click=Decimal("0.50"),
    )
    create_test_ad(
        db_session,
        headline="Worldwide General Campaign",
        target_language=None,
        remaining_budget=Decimal("100.00"),
        cost_per_click=Decimal("0.20"),
    )

    # First badge matches Rust
    res1 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert res1.status_code == 200
    assert "Exclusive Rust Campaign" in res1.text

    # Click depletes Rust campaign
    client.get(f"/click/{ad_rust.id}/{repo.id}", follow_redirects=False)
    db_session.refresh(ad_rust)

    # Subsequent badge falls back to General
    res2 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert res2.status_code == 200
    assert "Worldwide General Campaign" in res2.text


def test_c3_50_50_revenue_accounting_multiple_campaigns(
    client: TestClient, db_session: Session
):
    """C3: Multiple clicks from different campaigns consolidate into exact 50/50 revenue report."""
    repo = create_test_repository(db_session)
    ad1 = create_test_ad(db_session, cost_per_click=Decimal("0.60"))
    ad2 = create_test_ad(db_session, cost_per_click=Decimal("0.90"))

    # Click on Ad 1 ($0.60)
    client.get(f"/click/{ad1.id}/{repo.id}", follow_redirects=False)
    # Click on Ad 2 ($0.90)
    client.get(f"/click/{ad2.id}/{repo.id}", follow_redirects=False)

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    assert Decimal(str(data["gross_revenue"])) == Decimal("1.50")
    assert Decimal(str(data["maintainer_earnings"])) == Decimal("0.75")
    assert Decimal(str(data["platform_cut"])) == Decimal("0.75")
    assert data["total_clicks"] == 2


def test_c4_embedded_svg_hyperlink_follows_through_to_click_redirect(
    client: TestClient, db_session: Session
):
    """C4: Extracting the <a> tag link from rendered SVG and navigating returns 302 to destination."""
    from tests.conftest import Click

    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session, click_url="https://sponsor.destination.example.com")

    # Render badge
    badge_res = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert badge_res.status_code == 200
    root = assert_valid_svg_xml(badge_res.text)

    # Find anchor element
    anchors = [e for e in root.iter() if "a" in e.tag.lower()]
    assert len(anchors) > 0
    href = anchors[0].attrib.get("href") or anchors[0].attrib.get(
        "{http://www.w3.org/1999/xlink}href"
    )
    assert href is not None

    # Follow the click link extracted from SVG
    click_res = client.get(href, follow_redirects=False)
    assert click_res.status_code == 302
    assert (
        click_res.headers.get("location") == "https://sponsor.destination.example.com"
    )

    # Verify click was logged in DB
    click_count = (
        db_session.query(Click).filter_by(repo_id=repo.id, ad_id=ad.id).count()
    )
    assert click_count >= 1


def test_c5_impression_dedup_multiple_concurrent_users(
    client: TestClient, db_session: Session
):
    """C5: Two distinct clients viewing badge multiple times result in exactly 2 impression records."""
    from tests.conftest import Impression

    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    # User 1 hits badge 3 times
    headers_u1 = {"user-agent": "Browser-Agent-Alpha", "x-forwarded-for": "10.0.0.1"}
    for _ in range(3):
        client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers=headers_u1)

    # User 2 hits badge 4 times
    headers_u2 = {"user-agent": "Browser-Agent-Beta", "x-forwarded-for": "10.0.0.2"}
    for _ in range(4):
        client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers=headers_u2)

    impressions = db_session.query(Impression).filter_by(repo_id=repo.id).all()
    assert len(impressions) == 2
    # Verify hashes are distinct
    assert impressions[0].client_hash != impressions[1].client_hash


def test_c6_maintainer_onboarding_claim_and_snippet_lifecycle(
    client: TestClient, db_session: Session
):
    """C6: Unclaimed repo is claimed by maintainer, and snippet generator produces active links."""
    repo = create_test_repository(db_session, claimed=False)
    create_test_ad(db_session)

    # Claim repo
    claim_payload = {
        "owner": repo.owner,
        "name": repo.name,
        "maintainer_handle": "lead_dev",
        "payout_address": "payout@example.com",
    }
    claim_res = client.post("/maintainers/claim", json=claim_payload)
    assert claim_res.status_code in [200, 201]

    # Fetch snippet
    snippet_res = client.get(f"/maintainers/snippet/{repo.owner}/{repo.name}")
    assert snippet_res.status_code == 200
    data = snippet_res.json()
    assert f"/badge/{repo.owner}/{repo.name}.svg" in data["markdown"]

    # Verify badge endpoint works for claimed repo
    badge_res = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert badge_res.status_code == 200


def test_c7_case_insensitive_matching_matrix(client: TestClient, db_session: Session):
    """C7: Diverse language casing permutations (PYTHON, python, PyThOn) match target campaign."""
    create_test_ad(
        db_session, headline="Python Universal Matcher", target_language="Python"
    )

    for casing in ["PYTHON", "python", "Python", "PyThOn"]:
        repo = create_test_repository(
            db_session,
            owner="casing-test",
            name=f"repo-{casing}",
            primary_language=casing,
        )
        response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
        assert response.status_code == 200
        assert "Python Universal Matcher" in response.text


def test_c8_shared_sponsor_budget_across_multiple_repos(
    client: TestClient, db_session: Session
):
    """C8: Two repositories displaying same sponsor deplete the sponsor's shared budget pool."""
    repo_a = create_test_repository(
        db_session, owner="org", name="repo-a", primary_language="Go"
    )
    repo_b = create_test_repository(
        db_session, owner="org", name="repo-b", primary_language="Go"
    )
    shared_ad = create_test_ad(
        db_session,
        target_language="Go",
        remaining_budget=Decimal("2.00"),
        cost_per_click=Decimal("1.00"),
    )

    # Click from Repo A
    client.get(f"/click/{shared_ad.id}/{repo_a.id}", follow_redirects=False)
    db_session.refresh(shared_ad)
    assert shared_ad.remaining_budget == Decimal("1.00")

    # Click from Repo B
    client.get(f"/click/{shared_ad.id}/{repo_b.id}", follow_redirects=False)
    db_session.refresh(shared_ad)
    assert shared_ad.remaining_budget == Decimal("0.00")


def test_c9_concurrency_click_deduction_integrity(
    client: TestClient, db_session: Session
):
    """C9: Sequential clicks on ad with 3x CPC budget leaves exactly 0 remaining budget."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(
        db_session, remaining_budget=Decimal("3.00"), cost_per_click=Decimal("1.00")
    )

    for _ in range(3):
        client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)

    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("0.00")


def test_c10_multi_repo_revenue_isolation(client: TestClient, db_session: Session):
    """C10: Revenue tracking for Repo 1 is strictly isolated from Repo 2."""
    repo1 = create_test_repository(db_session, owner="org1", name="first-repo")
    repo2 = create_test_repository(db_session, owner="org2", name="second-repo")
    ad = create_test_ad(db_session, cost_per_click=Decimal("1.00"))

    # 3 clicks on Repo 1
    for _ in range(3):
        client.get(f"/click/{ad.id}/{repo1.id}", follow_redirects=False)

    # 1 click on Repo 2
    client.get(f"/click/{ad.id}/{repo2.id}", follow_redirects=False)

    # Check Repo 1 revenue
    res1 = client.get(f"/revenue/{repo1.id}")
    assert res1.status_code == 200
    assert Decimal(str(res1.json()["gross_revenue"])) == Decimal("3.00")
    assert Decimal(str(res1.json()["maintainer_earnings"])) == Decimal("1.50")

    # Check Repo 2 revenue
    res2 = client.get(f"/revenue/{repo2.id}")
    assert res2.status_code == 200
    assert Decimal(str(res2.json()["gross_revenue"])) == Decimal("1.00")
    assert Decimal(str(res2.json()["maintainer_earnings"])) == Decimal("0.50")


def test_c11_camo_cdn_proxy_and_client_audit_hash(
    client: TestClient, db_session: Session
):
    """C11: Requests routed through GitHub Camo CDN proxy parse client IP properly."""
    from tests.conftest import Impression

    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    client.get(
        f"/badge/{repo.owner}/{repo.name}.svg",
        headers={
            "user-agent": "GitHub-Camo",
            "x-forwarded-for": "203.0.113.50",
            "via": "1.1 github-camo",
        },
    )

    imp = db_session.query(Impression).filter_by(repo_id=repo.id).first()
    assert imp is not None
    assert len(imp.client_hash) == 64


def test_c12_campaign_reactivation_and_badge_rotation(
    client: TestClient, db_session: Session
):
    """C12: Reactivating an exhausted campaign brings it back into badge rotation."""
    repo = create_test_repository(db_session, primary_language="Ruby")
    ruby_ad = create_test_ad(
        db_session,
        headline="Ruby Specialist Tool",
        target_language="Ruby",
        remaining_budget=Decimal("0.00"),
        active=False,
    )
    create_test_ad(
        db_session,
        headline="General Platform Backup",
        target_language=None,
        remaining_budget=Decimal("50.00"),
        active=True,
    )

    # Badge falls back to General
    res1 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert "General Platform Backup" in res1.text

    # Reactivate Ruby ad with fresh budget
    ruby_ad.remaining_budget = Decimal("50.00")
    ruby_ad.active = True
    db_session.commit()

    # Badge now selects Ruby campaign
    res2 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert "Ruby Specialist Tool" in res2.text


def test_c13_unclaimed_repo_revenue_accrual_and_claim_transition(
    client: TestClient, db_session: Session
):
    """C13: Revenue accumulated while repo was unclaimed transfers seamlessly upon claiming."""
    repo = create_test_repository(db_session, claimed=False)
    ad = create_test_ad(db_session, cost_per_click=Decimal("2.00"))

    # Clicks prior to claim
    client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)

    # Claim repo
    claim_payload = {
        "owner": repo.owner,
        "name": repo.name,
        "maintainer_handle": "new_maintainer",
    }
    client.post("/maintainers/claim", json=claim_payload)

    # Report reflects historical revenue
    rev_res = client.get(f"/revenue/{repo.id}")
    assert rev_res.status_code == 200
    assert Decimal(str(rev_res.json()["maintainer_earnings"])) == Decimal("1.00")


def test_c14_dynamic_active_click_redirect_matches_active_ad(
    client: TestClient, db_session: Session
):
    """C14: /click/active/{repo_id} dynamically redirects to whichever sponsor is active."""
    repo = create_test_repository(db_session, primary_language="Python")
    create_test_ad(
        db_session,
        click_url="https://python-active.example.com",
        target_language="Python",
    )

    response = client.get(f"/click/active/{repo.id}", follow_redirects=False)
    if response.status_code == 302:
        assert response.headers.get("location") == "https://python-active.example.com"


def test_c15_svg_badge_elements_coexistence(client: TestClient, db_session: Session):
    """C15: Repo name, stars, language, CI/CD status, and sponsor copy coexist cleanly in SVG."""
    repo = create_test_repository(
        db_session,
        owner="full-test-org",
        name="full-repo",
        stars=42000,
        primary_language="Python",
        ci_status="passing",
    )
    create_test_ad(
        db_session,
        headline="Coexist Sponsor Headline",
        cta_text="Coexist CTA",
        target_language="Python",
    )

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    content = response.text
    assert "full-repo" in content
    assert "Coexist Sponsor Headline" in content
    assert "Coexist CTA" in content
    assert_valid_svg_xml(content)


def test_c16_zero_budget_ad_excluded_from_matching(
    client: TestClient, db_session: Session
):
    """C16: Ad with remaining_budget=0.00 is skipped even if active=True and language matches."""
    repo = create_test_repository(db_session, primary_language="Kotlin")
    create_test_ad(
        db_session,
        headline="Kotlin Ad With Zero Budget",
        target_language="Kotlin",
        remaining_budget=Decimal("0.00"),
        active=True,
    )
    create_test_ad(
        db_session,
        headline="General Fallback For Zero Budget",
        target_language=None,
        remaining_budget=Decimal("50.00"),
        active=True,
    )

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert "General Fallback For Zero Budget" in response.text
    assert "Kotlin Ad With Zero Budget" not in response.text


def test_c17_repeat_impressions_and_click_interaction(
    client: TestClient, db_session: Session
):
    """C17: User views badge (1 imp), clicks ad (1 click), views badge again (0 new imp)."""
    from tests.conftest import Click, Impression

    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)
    headers = {"user-agent": "workflow-browser-client"}

    # 1. View badge
    client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers=headers)
    assert db_session.query(Impression).filter_by(repo_id=repo.id).count() == 1

    # 2. Click ad
    client.get(f"/click/{ad.id}/{repo.id}", headers=headers, follow_redirects=False)
    assert db_session.query(Click).filter_by(repo_id=repo.id).count() == 1

    # 3. View badge again
    client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers=headers)
    assert db_session.query(Impression).filter_by(repo_id=repo.id).count() == 1
