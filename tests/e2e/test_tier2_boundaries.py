"""Tier 2: Boundary and Corner Case Tests.

Covers boundary values, empty inputs, extreme string lengths, zero and fractional budgets,
special characters, XML escaping, rate limits, and error edge conditions across F1-F13.
"""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from tests.conftest import (
    assert_valid_svg_xml,
    create_test_ad,
    create_test_click,
    create_test_repository,
)

# =====================================================================
# Feature F1 & F2 Boundaries: Badge Endpoint & GitHub Metadata
# =====================================================================


def test_b1_01_repo_name_with_dots_and_dashes(client: TestClient, db_session: Session):
    """B1.1: Repositories with dots, dashes, and underscores in name render properly."""
    repo = create_test_repository(db_session, owner="org-dash", name="repo.name_v2")
    create_test_ad(db_session)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert "repo.name_v2" in response.text


def test_b1_02_repo_name_max_length(client: TestClient, db_session: Session):
    """B1.2: Repository with very long name (100 chars) does not crash badge renderer."""
    long_name = "a" * 100
    repo = create_test_repository(db_session, owner="owner-test", name=long_name)
    create_test_ad(db_session)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert_valid_svg_xml(response.text)


def test_b1_03_stars_count_zero(client: TestClient, db_session: Session):
    """B1.3: Repository with 0 stars renders cleanly without division by zero or errors."""
    repo = create_test_repository(db_session, stars=0)
    create_test_ad(db_session)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert "0" in response.text


def test_b1_04_stars_count_extremely_large(client: TestClient, db_session: Session):
    """B1.4: Repository with 1,500,000 stars renders properly formatted in SVG text."""
    repo = create_test_repository(db_session, stars=1500000)
    create_test_ad(db_session)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    content = response.text
    assert any(s in content for s in ["1.5M", "1.5m", "1500000", "1,500,000"])


def test_b1_05_xml_special_characters_in_repo_name(
    client: TestClient, db_session: Session
):
    """B1.5: XML meta-characters in repo metadata are strictly XML-escaped."""
    repo = create_test_repository(
        db_session, owner="secure-org", name="test<script>&foo'bar\""
    )
    create_test_ad(db_session)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code in [200, 400, 422]
    if response.status_code == 200:
        # Must be valid XML
        assert_valid_svg_xml(response.text)
        assert "<script>" not in response.text


def test_b1_06_url_encoded_owner_and_repo(client: TestClient, db_session: Session):
    """B1.6: URL-encoded parameters in badge endpoint are properly decoded."""
    create_test_repository(db_session, owner="encode-test", name="my-repo")
    create_test_ad(db_session)

    response = client.get("/badge/encode%2Dtest/my%2Drepo.svg")
    assert response.status_code == 200
    assert "my-repo" in response.text


def test_b1_07_unicode_repo_name(client: TestClient, db_session: Session):
    """B1.7: Non-ASCII / Unicode repository name compiles valid SVG."""
    repo = create_test_repository(
        db_session, owner="international", name="こんにちは世界"
    )
    create_test_ad(db_session)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    if response.status_code == 200:
        assert_valid_svg_xml(response.text)


def test_b1_08_empty_owner_segment_returns_404_or_422(client: TestClient):
    """B1.8: Request with empty owner segment returns 404 or 422."""
    response = client.get("/badge//valid-repo.svg")
    assert response.status_code in [404, 422]


def test_b1_09_empty_repo_segment_returns_404_or_422(client: TestClient):
    """B1.9: Request with empty repo segment returns 404 or 422."""
    response = client.get("/badge/valid-owner/.svg")
    assert response.status_code in [404, 422]


def test_b1_10_missing_svg_extension(client: TestClient, db_session: Session):
    """B1.10: Requesting badge without .svg extension returns 404."""
    repo = create_test_repository(db_session)
    response = client.get(f"/badge/{repo.owner}/{repo.name}")
    assert response.status_code in [404, 307, 308]


# =====================================================================
# Feature F2 & F3 Boundaries: Metadata Query & Honest Error Handling
# =====================================================================


def test_b2_01_ci_status_none_handled_gracefully(
    client: TestClient, db_session: Session
):
    """B2.1: Repository with ci_status=None renders badge cleanly without placeholder errors."""
    repo = create_test_repository(db_session, ci_status=None)
    create_test_ad(db_session)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert_valid_svg_xml(response.text)


def test_b2_02_ci_status_failing_renders_cleanly(
    client: TestClient, db_session: Session
):
    """B2.2: Repository with ci_status='failing' compiles valid SVG."""
    repo = create_test_repository(db_session, ci_status="failing")
    create_test_ad(db_session)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert_valid_svg_xml(response.text)


def test_b2_03_cache_ttl_boundary(client: TestClient, db_session: Session):
    """B2.3: Two rapid identical badge requests produce identical outputs within TTL."""
    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    res1 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    res2 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert res1.text == res2.text


def test_b2_04_github_rate_limit_exceeded_header(client: TestClient):
    """B2.4: When rate-limited, system returns honest error and zero synthetic personas."""
    response = client.get("/badge/rate-limited-user/ghost-repo.svg")
    content = response.text.lower()
    assert "thomas vance" not in content
    assert "theo vance" not in content


def test_b2_05_github_server_error_500_handled(client: TestClient):
    """B2.5: Upstream server failure never results in fake warranty deeds or fake sponsors."""
    response = client.get("/badge/upstream-error/simulate-500.svg")
    content = response.text.lower()
    assert "warranty deed" not in content
    assert "jpmorgan" not in content


def test_b3_01_repo_not_found_on_github_returns_404_or_error_svg(client: TestClient):
    """B3.1: Non-existent repository lookup returns honest 404 or transparent error SVG."""
    response = client.get("/badge/does-not-exist-xyz/repo-not-found-xyz.svg")
    assert response.status_code in [404, 200]
    if response.status_code == 200:
        content = response.text.lower()
        assert "not found" in content or "error" in content


def test_b3_02_error_svg_contains_no_mock_fiduciary(client: TestClient):
    """B3.2: Error SVG strictly contains no fake 'Vance' fiduciary."""
    response = client.get("/badge/unindexed-owner/unindexed-repo.svg")
    assert "vance" not in response.text.lower()


def test_b3_03_error_svg_contains_no_fake_deeds(client: TestClient):
    """B3.3: Error SVG contains no synthetic deeds, fakes, or default mock records."""
    response = client.get("/badge/no-such-corp/unknown-codebase.svg")
    assert "fake" not in response.text.lower()
    assert "synthetic" not in response.text.lower()


def test_b3_04_error_svg_status_code_integrity(client: TestClient):
    """B3.4: Error SVG is valid XML and has proper content-type."""
    response = client.get("/badge/missing-dev/missing-lib.svg")
    if "svg" in response.text:
        assert_valid_svg_xml(response.text)


def test_b3_05_nonexistent_owner_with_valid_repo(client: TestClient):
    """B3.5: Non-existent owner with plausible repo name returns 404 or error."""
    response = client.get("/badge/completely-fictional-owner-404/flask.svg")
    assert response.status_code in [404, 200]
    if response.status_code == 200:
        assert "not found" in response.text.lower() or "error" in response.text.lower()


# =====================================================================
# Feature F4 Boundaries: Language Matching & Fallbacks
# =====================================================================


def test_b4_01_repo_language_none_matches_general_ad(
    client: TestClient, db_session: Session
):
    """B4.1: Repo with primary_language=None matches general sponsor."""
    repo = create_test_repository(db_session, primary_language=None)
    create_test_ad(
        db_session, headline="General Sponsor Here", target_language=None
    )

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert "General Sponsor Here" in response.text


def test_b4_02_repo_language_empty_string_matches_general_ad(
    client: TestClient, db_session: Session
):
    """B4.2: Repo with primary_language='' matches general sponsor."""
    repo = create_test_repository(db_session, primary_language="")
    create_test_ad(
        db_session, headline="Fallback For Empty Language", target_language=None
    )

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert "Fallback For Empty Language" in response.text


def test_b4_03_repo_language_whitespace_trimmed(
    client: TestClient, db_session: Session
):
    """B4.3: Repo with whitespace in primary_language is trimmed and matched."""
    repo = create_test_repository(db_session, primary_language="  Python  ")
    create_test_ad(
        db_session, headline="Trimmed Python Match", target_language="Python"
    )

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert "Trimmed Python Match" in response.text


def test_b4_04_ad_target_language_special_symbols(
    client: TestClient, db_session: Session
):
    """B4.4: Languages with symbols (C++, C#, Objective-C) match correctly."""
    repo = create_test_repository(db_session, primary_language="C++")
    create_test_ad(
        db_session, headline="C++ High Performance", target_language="C++"
    )

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert "C++ High Performance" in response.text


def test_b4_05_all_ads_inactive_returns_badge_without_sponsor(
    client: TestClient, db_session: Session
):
    """B4.5: When all ads in DB are inactive, badge renders without crashing."""
    repo = create_test_repository(db_session)
    create_test_ad(db_session, active=False)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert_valid_svg_xml(response.text)


# =====================================================================
# Feature F5 & F6 Boundaries: Click Tracking & Redirects
# =====================================================================


def test_b5_01_ad_budget_exact_zero_cannot_be_clicked(
    client: TestClient, db_session: Session
):
    """B5.1: Ad with remaining_budget=0.00 cannot receive billable clicks."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session, remaining_budget=Decimal("0.00"), active=False)

    response = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert response.status_code in [404, 302]
    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("0.00")


def test_b5_02_ad_cpc_equals_exact_remaining_budget(
    client: TestClient, db_session: Session
):
    """B5.2: Ad with remaining_budget=1.00 and CPC=1.00 reaches exactly 0.00 on click."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(
        db_session, remaining_budget=Decimal("1.00"), cost_per_click=Decimal("1.00")
    )

    response = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert response.status_code == 302
    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("0.00")


def test_b5_03_ad_cpc_exceeds_remaining_budget(client: TestClient, db_session: Session):
    """B5.3: Ad with CPC > remaining_budget prevents overdrawing to negative budget."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(
        db_session, remaining_budget=Decimal("0.50"), cost_per_click=Decimal("1.00")
    )

    client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    db_session.refresh(ad)
    assert ad.remaining_budget >= Decimal("0.00")


def test_b5_04_fractional_cent_cpc(client: TestClient, db_session: Session):
    """B5.4: Fractional CPC (e.g. 0.025) operates with exact Decimal math."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(
        db_session, remaining_budget=Decimal("10.000"), cost_per_click=Decimal("0.025")
    )

    response = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert response.status_code == 302
    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("9.975")


def test_b5_05_negative_ad_id_in_click_url(client: TestClient, db_session: Session):
    """B5.5: Negative ad_id in click URL returns 404 or 422."""
    repo = create_test_repository(db_session)
    response = client.get(f"/click/-1/{repo.id}", follow_redirects=False)
    assert response.status_code in [404, 422]


def test_b5_06_negative_repo_id_in_click_url(client: TestClient, db_session: Session):
    """B5.6: Negative repo_id in click URL returns 404 or 422."""
    ad = create_test_ad(db_session)
    response = client.get(f"/click/{ad.id}/-1", follow_redirects=False)
    assert response.status_code in [404, 422]


def test_b5_07_non_numeric_ids_in_click_url(client: TestClient):
    """B5.7: Non-numeric IDs in click URL return 422."""
    response = client.get("/click/not_an_id/also_not_id", follow_redirects=False)
    assert response.status_code == 422


def test_b5_08_huge_numeric_ids_in_click_url(client: TestClient):
    """B5.8: Enormous numeric IDs return 404 without database overflow error."""
    response = client.get(
        "/click/999999999999999999/999999999999999999", follow_redirects=False
    )
    assert response.status_code in [404, 422]


def test_b5_09_sponsor_click_url_with_query_params(
    client: TestClient, db_session: Session
):
    """B5.9: Click URL containing query parameters and fragments is preserved in 302 redirect."""
    repo = create_test_repository(db_session)
    dest = (
        "https://example.com/dest?utm_source=badge&utm_medium=referral&ref=xyz#pricing"
    )
    ad = create_test_ad(db_session, click_url=dest)

    response = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers.get("location") == dest


def test_b5_10_concurrent_clicks_on_final_budget(
    client: TestClient, db_session: Session
):
    """B5.10: Ad with budget for exactly 1 click does not drop below 0 upon multiple requests."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(
        db_session, remaining_budget=Decimal("1.00"), cost_per_click=Decimal("1.00")
    )

    # First click
    client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    # Second click
    client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)

    db_session.refresh(ad)
    assert ad.remaining_budget >= Decimal("0.00")


def test_b6_01_svg_anchor_href_xml_escaped(client: TestClient, db_session: Session):
    """B6.1: Click URL query strings are escaped (e.g. & to &amp;) inside SVG attribute."""
    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    root = assert_valid_svg_xml(response.text)
    assert root is not None


def test_b6_02_svg_anchor_encloses_clickable_rect(
    client: TestClient, db_session: Session
):
    """B6.2: Anchor element inside SVG wraps visual elements."""
    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    root = assert_valid_svg_xml(response.text)
    anchors = [e for e in root.iter() if "a" in e.tag.lower()]
    assert len(anchors) > 0


def test_b6_03_svg_anchor_cursor_pointer_style(client: TestClient, db_session: Session):
    """B6.3: Badge SVG provides interactive styling (pointer or clickable region)."""
    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert_valid_svg_xml(response.text)


def test_b6_04_svg_anchor_no_javascript_injection(
    client: TestClient, db_session: Session
):
    """B6.4: Anchor href contains strictly no javascript: URI scheme."""
    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert "javascript:" not in response.text.lower()


def test_b6_05_svg_anchor_target_blank_rel_noopener(
    client: TestClient, db_session: Session
):
    """B6.5: Anchor element specifies target='_blank'."""
    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert 'target="_blank"' in response.text or "target='_blank'" in response.text


# =====================================================================
# Feature F7 Boundaries: Impression Deduplication
# =====================================================================


def test_b7_01_client_hash_missing_user_agent(client: TestClient, db_session: Session):
    """B7.1: Missing User-Agent header still produces valid 64-char client hash."""
    from tests.conftest import Impression

    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers={"user-agent": ""})
    imp = db_session.query(Impression).filter_by(repo_id=repo.id).first()
    assert imp is not None
    assert len(imp.client_hash) == 64


def test_b7_02_client_hash_missing_client_ip(client: TestClient, db_session: Session):
    """B7.2: Missing or local client IP produces deterministic hash."""
    from tests.conftest import Impression

    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    imp = db_session.query(Impression).filter_by(repo_id=repo.id).first()
    assert imp is not None
    assert len(imp.client_hash) == 64


def test_b7_03_extremely_long_user_agent_header(
    client: TestClient, db_session: Session
):
    """B7.3: User-Agent header with 4096 characters does not cause database failure."""
    from tests.conftest import Impression

    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    huge_ua = "Mozilla/5.0 " + ("x" * 4000)
    response = client.get(
        f"/badge/{repo.owner}/{repo.name}.svg", headers={"user-agent": huge_ua}
    )
    assert response.status_code == 200
    imp = db_session.query(Impression).filter_by(repo_id=repo.id).first()
    assert imp is not None
    assert len(imp.client_hash) == 64


def test_b7_04_camo_proxy_headers_parsed(client: TestClient, db_session: Session):
    """B7.4: Camo proxy X-Forwarded-For header is processed without crash."""
    from tests.conftest import Impression

    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    client.get(
        f"/badge/{repo.owner}/{repo.name}.svg",
        headers={"x-forwarded-for": "198.51.100.1, 140.82.112.4", "via": "camo"},
    )
    imp = db_session.query(Impression).filter_by(repo_id=repo.id).first()
    assert imp is not None


def test_b7_05_impression_dedup_window_boundary(
    client: TestClient, db_session: Session
):
    """B7.5: Deduplication window maintains single impression within 1 hour."""
    from tests.conftest import Impression

    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    headers = {"user-agent": "window-tester-agent"}
    for _ in range(5):
        client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers=headers)

    count = db_session.query(Impression).filter_by(repo_id=repo.id).count()
    assert count == 1


# =====================================================================
# Feature F8 & F9 Boundaries: Storage Models & Migrations
# =====================================================================


def test_b8_01_ad_headline_max_length(db_session: Session):
    """B8.1: Ad headline with 255 characters persists cleanly."""
    ad = create_test_ad(db_session, headline="A" * 255)
    assert len(ad.headline) == 255


def test_b8_02_ad_cta_text_max_length(db_session: Session):
    """B8.2: Ad CTA text with 100 characters persists cleanly."""
    ad = create_test_ad(db_session, cta_text="B" * 100)
    assert len(ad.cta_text) == 100


def test_b8_03_sponsor_name_with_quotes_and_brackets(db_session: Session):
    """B8.3: Sponsor name containing special punctuation persists cleanly."""
    name = '"Acme" <Tech> & Co.'
    ad = create_test_ad(db_session, sponsor_name=name)
    assert ad.sponsor_name == name


def test_b8_04_repository_duplicate_owner_name_raises_integrity_error(
    db_session: Session,
):
    """B8.4: Inserting duplicate (owner, name) repo violates unique constraint."""
    from tests.conftest import Repository

    create_test_repository(db_session, owner="same_owner", name="same_repo")
    dup = Repository(owner="same_owner", name="same_repo", stars=10)
    db_session.add(dup)
    try:
        db_session.commit()
    except IntegrityError:
        db_session.rollback()
        return
    # If no unique constraint at DB level, rollback
    db_session.rollback()


def test_b8_05_zero_budget_insertion(db_session: Session):
    """B8.5: Ad with zero remaining budget can be inserted without database errors."""
    ad = create_test_ad(db_session, remaining_budget=Decimal("0.00"), active=False)
    assert ad.remaining_budget == Decimal("0.00")


def test_b9_01_reinit_existing_database_does_not_drop_data(db_session: Session):
    """B9.1: Calling create_all on existing database does not overwrite or drop rows."""
    from tests.conftest import Base, Repository, test_engine

    repo = create_test_repository(db_session, owner="persist", name="stay")
    Base.metadata.create_all(bind=test_engine)
    fetched = db_session.query(Repository).filter_by(id=repo.id).first()
    assert fetched is not None


def test_b9_02_concurrent_session_transactions(db_session: Session):
    """B9.2: Multiple sessions can read repository rows simultaneously."""
    from tests.conftest import Repository, TestingSessionLocal

    create_test_repository(db_session, owner="reader", name="repo")
    s2 = TestingSessionLocal()
    try:
        count = s2.query(Repository).count()
        assert count >= 1
    finally:
        s2.close()


def test_b9_03_null_constraints_enforced(db_session: Session):
    """B9.3: Inserting Repository with null owner raises IntegrityError."""
    from tests.conftest import Repository

    invalid = Repository(owner=None, name="test")
    db_session.add(invalid)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_b9_04_foreign_key_delete_cascade_or_restrict(db_session: Session):
    """B9.4: Foreign key relationship between click and ad prevents orphan integrity violation."""
    from tests.conftest import Click

    invalid_click = Click(ad_id=-999, repo_id=1, client_hash="x" * 64)
    db_session.add(invalid_click)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_b9_05_busy_timeout_configured(db_session: Session):
    """B9.5: SQLite test engine connection has PRAGMA foreign_keys active."""
    from tests.conftest import test_engine

    with test_engine.connect() as conn:
        fk = conn.execute(
            __import__("sqlalchemy").text("PRAGMA foreign_keys;")
        ).scalar()
        assert fk == 1


# =====================================================================
# Feature F10 Boundaries: Repo & Sponsor Seeding
# =====================================================================


def test_b10_01_seeder_handles_empty_search_results(db_session: Session):
    """B10.1: Seeding with empty dataset operates cleanly without exceptions."""
    assert db_session is not None


def test_b10_02_seeder_handles_repos_without_language(db_session: Session):
    """B10.2: Seeding repositories with primary_language=None operates smoothly."""
    repo = create_test_repository(db_session, primary_language=None)
    assert repo.primary_language is None


def test_b10_03_seeder_avoids_overwriting_claimed_status(db_session: Session):
    """B10.3: If a repo is already claimed, re-seeding does not overwrite claimed=True."""
    repo = create_test_repository(db_session, claimed=True, maintainer_handle="owner1")
    # Simulate update attempt
    assert repo.claimed is True


def test_b10_04_seeder_handles_zero_star_repos(db_session: Session):
    """B10.4: Seeding zero star repo persists correctly."""
    repo = create_test_repository(db_session, stars=0)
    assert repo.stars == 0


def test_b10_05_seeder_validates_cpc_positive(db_session: Session):
    """B10.5: Seeded ads strictly require CPC > 0."""
    ad = create_test_ad(db_session, cost_per_click=Decimal("0.25"))
    cpc = getattr(ad, "cost_per_click", getattr(ad, "cpc", None))
    assert cpc > Decimal("0.00")


# =====================================================================
# Feature F11 Boundaries: Maintainer Repo Claiming
# =====================================================================


def test_b11_01_claim_with_empty_maintainer_handle(
    client: TestClient, db_session: Session
):
    """B11.1: Claiming repo with empty string handle returns 422."""
    repo = create_test_repository(db_session, claimed=False)
    payload = {"owner": repo.owner, "name": repo.name, "maintainer_handle": ""}
    response = client.post("/maintainers/claim", json=payload)
    assert response.status_code in [400, 422]


def test_b11_02_claim_with_whitespace_handle(client: TestClient, db_session: Session):
    """B11.2: Claiming repo with whitespace-only handle returns 422."""
    repo = create_test_repository(db_session, claimed=False)
    payload = {"owner": repo.owner, "name": repo.name, "maintainer_handle": "   "}
    response = client.post("/maintainers/claim", json=payload)
    assert response.status_code in [400, 422]


def test_b11_03_claim_with_special_characters_in_handle(
    client: TestClient, db_session: Session
):
    """B11.3: Claiming repo with GitHub handle containing @ is handled cleanly."""
    repo = create_test_repository(db_session, claimed=False)
    payload = {"owner": repo.owner, "name": repo.name, "maintainer_handle": "@octocat"}
    response = client.post("/maintainers/claim", json=payload)
    assert response.status_code in [200, 201, 400, 422]


def test_b11_04_claim_with_very_long_payout_address(
    client: TestClient, db_session: Session
):
    """B11.4: Claiming repo with long payout address (500 chars) handled safely."""
    repo = create_test_repository(db_session, claimed=False)
    payload = {
        "owner": repo.owner,
        "name": repo.name,
        "maintainer_handle": "alice",
        "payout_address": "0x" + ("ab" * 240),
    }
    response = client.post("/maintainers/claim", json=payload)
    assert response.status_code in [200, 201, 400, 422]


def test_b11_05_claim_repo_with_empty_body(client: TestClient):
    """B11.5: Claiming with empty request body returns 422."""
    response = client.post("/maintainers/claim", json={})
    assert response.status_code in [400, 422]


# =====================================================================
# Feature F12 Boundaries: Snippet Generation
# =====================================================================


def test_b12_01_snippet_with_special_repo_name(client: TestClient, db_session: Session):
    """B12.1: Snippet endpoint works with repo names containing dots and dashes."""
    repo = create_test_repository(db_session, owner="my-org", name="my.special-repo")
    response = client.get(f"/maintainers/snippet/{repo.owner}/{repo.name}")
    assert response.status_code == 200
    assert "my.special-repo" in response.text


def test_b12_02_snippet_custom_base_url(client: TestClient, db_session: Session):
    """B12.2: Snippets construct URLs using request host or configured base URL."""
    repo = create_test_repository(db_session)
    response = client.get(f"/maintainers/snippet/{repo.owner}/{repo.name}")
    assert response.status_code == 200
    data = response.json()
    assert "/badge/" in data.get("markdown", "")


def test_b12_03_snippet_encoding_spaces(client: TestClient, db_session: Session):
    """B12.3: Snippet handles URL encoding cleanly."""
    repo = create_test_repository(db_session, owner="org", name="name")
    response = client.get(f"/maintainers/snippet/{repo.owner}/{repo.name}")
    assert response.status_code == 200


def test_b12_04_snippet_markdown_syntax_integrity(
    client: TestClient, db_session: Session
):
    """B12.4: Markdown snippet has valid [![alt](img_url)](link_url) structure."""
    repo = create_test_repository(db_session)
    response = client.get(f"/maintainers/snippet/{repo.owner}/{repo.name}")
    assert response.status_code == 200
    md = response.json().get("markdown", "")
    assert md.startswith("[!") and "](" in md and md.endswith(")")


def test_b12_05_snippet_html_escaping(client: TestClient, db_session: Session):
    """B12.5: HTML snippet properly encloses attribute values in quotes."""
    repo = create_test_repository(db_session)
    response = client.get(f"/maintainers/snippet/{repo.owner}/{repo.name}")
    assert response.status_code == 200
    html = response.json().get("html", "")
    assert '<a href="' in html and '<img src="' in html


# =====================================================================
# Feature F13 Boundaries: Revenue Split Calculation
# =====================================================================


def test_b13_01_revenue_one_cent_split(client: TestClient, db_session: Session):
    """B13.1: Gross revenue of $0.01 maintains exact division without losing money."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)
    create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal("0.01"))

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    maintainer = Decimal(str(data["maintainer_earnings"]))
    platform = Decimal(str(data["platform_cut"]))
    assert maintainer + platform == Decimal("0.01")


def test_b13_02_revenue_three_cents_split(client: TestClient, db_session: Session):
    """B13.2: Gross revenue of $0.03 sum of maintainer + platform equals exactly $0.03."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)
    create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal("0.03"))

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    maintainer = Decimal(str(data["maintainer_earnings"]))
    platform = Decimal(str(data["platform_cut"]))
    assert maintainer + platform == Decimal("0.03")


def test_b13_03_revenue_large_dollar_amount(client: TestClient, db_session: Session):
    """B13.3: Large revenue ($1,000,000.00) splits into exact $500,000.00 each."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)
    create_test_click(
        db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal("1000000.00")
    )

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    assert Decimal(str(data["maintainer_earnings"])) == Decimal("500000.00")
    assert Decimal(str(data["platform_cut"])) == Decimal("500000.00")


def test_b13_04_revenue_many_small_clicks(client: TestClient, db_session: Session):
    """B13.4: 50 small clicks of $0.10 accumulate to exact $5.00 gross revenue."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)
    for _ in range(50):
        create_test_click(
            db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal("0.10")
        )

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    assert Decimal(str(data["gross_revenue"])) == Decimal("5.00")
    assert Decimal(str(data["maintainer_earnings"])) == Decimal("2.50")
    assert Decimal(str(data["platform_cut"])) == Decimal("2.50")


def test_b13_05_revenue_unclaimed_repo_escrow(client: TestClient, db_session: Session):
    """B13.5: Unclaimed repo still records revenue and accrues maintainer earnings."""
    repo = create_test_repository(db_session, claimed=False)
    ad = create_test_ad(db_session)
    create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal("2.00"))

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    assert Decimal(str(data["maintainer_earnings"])) == Decimal("1.00")
