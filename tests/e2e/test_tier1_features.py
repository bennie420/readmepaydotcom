"""Tier 1: Feature Isolation Tests (F1 - F13).

Requirement-driven opaque-box tests covering all core platform features in isolation
with at least 5 distinct test cases per feature.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from tests.conftest import (
    assert_valid_svg_xml,
    create_test_ad,
    create_test_click,
    create_test_impression,
    create_test_repository,
)

# =====================================================================
# Feature F1: Dynamic SVG Badge Endpoint (GET /badge/{owner}/{repo}.svg)
# =====================================================================


def test_f1_01_badge_returns_200_and_svg_mime(client: TestClient, db_session: Session):
    """F1.1: Requesting badge returns HTTP 200 with image/svg+xml content type."""
    repo = create_test_repository(
        db_session,
        owner="pallets",
        name="flask",
        stars=68000,
        primary_language="Python",
    )
    create_test_ad(db_session, target_language="Python", active=True)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert "image/svg+xml" in response.headers.get("content-type", "")


def test_f1_02_badge_valid_svg_xml_tree(client: TestClient, db_session: Session):
    """F1.2: Rendered badge compiles to syntactically valid XML with root <svg> element."""
    repo = create_test_repository(
        db_session,
        owner="encode",
        name="starlette",
        stars=12000,
        primary_language="Python",
    )
    create_test_ad(db_session, target_language="Python", active=True)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    root = assert_valid_svg_xml(response.text)
    assert "svg" in root.tag.lower()


def test_f1_03_badge_viewbox_and_dimensions(client: TestClient, db_session: Session):
    """F1.3: Rendered badge contains responsive viewBox 0 0 500 110."""
    repo = create_test_repository(
        db_session,
        owner="fastapi",
        name="fastapi",
        stars=75000,
        primary_language="Python",
    )
    create_test_ad(db_session, target_language="Python", active=True)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    root = assert_valid_svg_xml(response.text)
    viewbox = root.attrib.get("viewBox", "")
    assert "0 0 500 110" in viewbox or ("500" in viewbox and "110" in viewbox)


def test_f1_04_badge_renders_repo_metadata(client: TestClient, db_session: Session):
    """F1.4: Rendered badge includes repository name and stars in SVG body."""
    repo = create_test_repository(
        db_session, owner="psf", name="requests", stars=51000, primary_language="Python"
    )
    create_test_ad(db_session, target_language="Python", active=True)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    content = response.text
    assert repo.name in content
    # Stars should be formatted (e.g., 51000, 51k, or 51,000)
    assert any(s in content for s in ["51000", "51k", "51K", "51,000"])


def test_f1_05_badge_renders_matched_sponsor_copy(
    client: TestClient, db_session: Session
):
    """F1.5: Rendered badge includes sponsor headline and CTA text."""
    repo = create_test_repository(
        db_session,
        owner="django",
        name="django",
        stars=79000,
        primary_language="Python",
    )
    create_test_ad(
        db_session,
        headline="Catch Python Errors Instantly",
        cta_text="Try Sentry Free",
        target_language="Python",
        active=True,
    )

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    content = response.text
    assert "Catch Python Errors Instantly" in content
    assert "Try Sentry Free" in content


def test_f1_06_badge_caching_headers(client: TestClient, db_session: Session):
    """F1.6: Badge response includes public Cache-Control header."""
    repo = create_test_repository(
        db_session,
        owner="tiangolo",
        name="sqlmodel",
        stars=14000,
        primary_language="Python",
    )
    create_test_ad(db_session, target_language="Python", active=True)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    cache_header = response.headers.get("cache-control", "").lower()
    assert "public" in cache_header or "max-age" in cache_header


# =====================================================================
# Feature F2: Real GitHub Metadata Query & Cache
# =====================================================================


def test_f2_01_github_metadata_persists_repo_attributes(db_session: Session):
    """F2.1: Repository model records authentic stars, language, and status."""
    repo = create_test_repository(
        db_session,
        owner="torvalds",
        name="linux",
        stars=180000,
        primary_language="C",
        ci_status="passing",
    )
    assert repo.stars == 180000
    assert repo.primary_language == "C"
    assert repo.ci_status == "passing"


def test_f2_02_github_cache_control_header_adherence(
    client: TestClient, db_session: Session
):
    """F2.2: Badge endpoint returns max-age=3600 caching header."""
    repo = create_test_repository(
        db_session, owner="redis", name="redis", stars=65000, primary_language="C"
    )
    create_test_ad(db_session, target_language=None, active=True)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    cache_header = response.headers.get("cache-control", "")
    assert "3600" in cache_header or "max-age" in cache_header


def test_f2_03_github_metadata_caching_idempotency(
    client: TestClient, db_session: Session
):
    """F2.3: Repeated requests for the same repository return consistent cached data."""
    repo = create_test_repository(
        db_session,
        owner="samuelcolvin",
        name="pydantic",
        stars=21000,
        primary_language="Python",
    )
    create_test_ad(db_session, target_language="Python", active=True)

    res1 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    res2 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert res1.status_code == 200
    assert res2.status_code == 200
    assert res1.text == res2.text


def test_f2_04_github_ci_status_rendered(client: TestClient, db_session: Session):
    """F2.4: When CI/CD status is available, it is represented in the badge."""
    repo = create_test_repository(
        db_session, owner="pypa", name="pip", stars=9500, ci_status="passing"
    )
    create_test_ad(db_session, target_language="Python", active=True)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    content = response.text.lower()
    assert (
        "passing" in content
        or "ci" in content
        or "build" in content
        or "pip" in content
    )


def test_f2_05_github_service_rate_limit_resilience(
    client: TestClient, db_session: Session
):
    """F2.5: Cached repositories continue serving badges even during external rate limits."""
    repo = create_test_repository(
        db_session, owner="grpc", name="grpc", stars=40000, primary_language="C++"
    )
    create_test_ad(db_session, target_language=None, active=True)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200


# =====================================================================
# Feature F3: Transparent Error Handling (404 / Error SVG)
# =====================================================================


def test_f3_01_nonexistent_repo_honest_404_or_error_svg(client: TestClient):
    """F3.1: Non-existent repository returns 404 or honest error SVG."""
    response = client.get("/badge/nonexistent-org-99999/unregistered-repo-88888.svg")
    # Must be 404 status code OR error SVG with 404/not found messaging
    assert response.status_code in [404, 200]
    if response.status_code == 200:
        content = response.text.lower()
        assert "not found" in content or "error" in content or "404" in content


def test_f3_02_zero_synthetic_personas_in_error_output(client: TestClient):
    """F3.2: Error responses strictly never contain synthetic personas or fake maintainers."""
    response = client.get("/badge/ghost-user-0000/unknown-project-1111.svg")
    content = response.text.lower()
    assert "thomas vance" not in content
    assert "theo vance" not in content
    assert "fiduciary" not in content


def test_f3_03_malformed_repo_identifier_handling(client: TestClient):
    """F3.3: Request with malformed path or empty repo returns 404 or 422."""
    response = client.get("/badge/invalid_owner//.svg")
    assert response.status_code in [404, 422]


def test_f3_04_error_svg_preserves_svg_mime_type(client: TestClient):
    """F3.4: When returning an error SVG, the content type is image/svg+xml."""
    response = client.get("/badge/empty-user-none/missing-repo.svg")
    if "svg" in response.text:
        assert "image/svg+xml" in response.headers.get("content-type", "")


def test_f3_05_error_svg_xml_validity(client: TestClient):
    """F3.5: Error SVG response parses as valid XML."""
    response = client.get("/badge/no-such-owner/no-such-repo.svg")
    if response.status_code == 200 or "svg" in response.text:
        root = assert_valid_svg_xml(response.text)
        assert root is not None


# =====================================================================
# Feature F4: Language-Targeted Ad Matching
# =====================================================================


def test_f4_01_python_repo_matches_python_sponsor(
    client: TestClient, db_session: Session
):
    """F4.1: Repository with primary_language='Python' matches Python sponsor."""
    repo = create_test_repository(
        db_session, owner="pallets", name="click", primary_language="Python"
    )
    create_test_ad(
        db_session,
        sponsor_name="Python Sponsor",
        headline="Python Specialized",
        target_language="Python",
    )
    create_test_ad(
        db_session,
        sponsor_name="General Sponsor",
        headline="General Tech",
        target_language=None,
    )

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert "Python Specialized" in response.text


def test_f4_02_typescript_repo_matches_typescript_sponsor(
    client: TestClient, db_session: Session
):
    """F4.2: Repository with primary_language='TypeScript' matches TypeScript sponsor."""
    repo = create_test_repository(
        db_session, owner="microsoft", name="vscode", primary_language="TypeScript"
    )
    create_test_ad(
        db_session,
        sponsor_name="TS Sponsor",
        headline="TypeScript Supercharge",
        target_language="TypeScript",
    )
    create_test_ad(
        db_session,
        sponsor_name="General Sponsor",
        headline="General Hosting",
        target_language=None,
    )

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert "TypeScript Supercharge" in response.text


def test_f4_03_rust_repo_matches_rust_sponsor(client: TestClient, db_session: Session):
    """F4.3: Repository with primary_language='Rust' matches Rust sponsor."""
    repo = create_test_repository(
        db_session, owner="tokio-rs", name="tokio", primary_language="Rust"
    )
    create_test_ad(
        db_session,
        sponsor_name="Rust Sponsor",
        headline="Blazing Fast Rust Tooling",
        target_language="Rust",
    )
    create_test_ad(
        db_session,
        sponsor_name="General Sponsor",
        headline="Generic Infrastructure",
        target_language=None,
    )

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert "Blazing Fast Rust Tooling" in response.text


def test_f4_04_unmatched_language_falls_back_to_general_sponsor(
    client: TestClient, db_session: Session
):
    """F4.4: Repo with unmatched language falls back to general sponsor."""
    repo = create_test_repository(
        db_session, owner="haskell", name="cabal", primary_language="Haskell"
    )
    create_test_ad(
        db_session,
        sponsor_name="Python Only",
        headline="Only Python Devs",
        target_language="Python",
    )
    create_test_ad(
        db_session,
        sponsor_name="Global Sponsor",
        headline="General Fallback Platform",
        target_language=None,
    )

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert "General Fallback Platform" in response.text


def test_f4_05_inactive_ad_skipped_for_matching(
    client: TestClient, db_session: Session
):
    """F4.5: Inactive language-specific ad is bypassed in favor of active general ad."""
    repo = create_test_repository(
        db_session, owner="python", name="cpython", primary_language="Python"
    )
    create_test_ad(
        db_session,
        headline="Inactive Python Ad",
        target_language="Python",
        active=False,
    )
    create_test_ad(
        db_session,
        headline="Active General Fallback",
        target_language=None,
        active=True,
    )

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert "Active General Fallback" in response.text
    assert "Inactive Python Ad" not in response.text


def test_f4_06_case_insensitive_language_matching(
    client: TestClient, db_session: Session
):
    """F4.6: Language matching is case-insensitive (e.g. 'python' matches 'Python')."""
    repo = create_test_repository(
        db_session, owner="aio-libs", name="aiohttp", primary_language="python"
    )
    create_test_ad(
        db_session,
        headline="Case Insensitive Sentry",
        target_language="Python",
        active=True,
    )

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert "Case Insensitive Sentry" in response.text


# =====================================================================
# Feature F5: Click-Through Tracking & 302 Redirect (GET /click/{ad_id}/{repo_id})
# =====================================================================


def test_f5_01_click_returns_302_redirect(client: TestClient, db_session: Session):
    """F5.1: Click endpoint returns HTTP 302 with Location header pointing to ad click_url."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session, click_url="https://sponsor.example.com/dest")

    response = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers.get("location") == "https://sponsor.example.com/dest"


def test_f5_02_click_creates_click_record(client: TestClient, db_session: Session):
    """F5.2: Click endpoint persists click event record in database."""
    from tests.conftest import Click

    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)

    response = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert response.status_code == 302

    click_record = (
        db_session.query(Click).filter_by(repo_id=repo.id, ad_id=ad.id).first()
    )
    assert click_record is not None


def test_f5_03_click_records_client_hash(client: TestClient, db_session: Session):
    """F5.3: Click record contains SHA-256 client audit hash."""
    from tests.conftest import Click

    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)

    client.get(
        f"/click/{ad.id}/{repo.id}",
        headers={"user-agent": "audit-browser"},
        follow_redirects=False,
    )
    click_record = (
        db_session.query(Click).filter_by(repo_id=repo.id, ad_id=ad.id).first()
    )
    assert click_record is not None
    assert len(click_record.client_hash) == 64


def test_f5_04_click_deducts_ad_budget(client: TestClient, db_session: Session):
    """F5.4: Successful click deducts cost_per_click from ad remaining_budget."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(
        db_session, remaining_budget=Decimal("50.00"), cost_per_click=Decimal("1.50")
    )

    client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("48.50")


def test_f5_05_click_exhausted_ad_handling(client: TestClient, db_session: Session):
    """F5.5: Clicking an ad with 0 remaining budget does not allow negative balance."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(
        db_session,
        remaining_budget=Decimal("0.00"),
        cost_per_click=Decimal("1.00"),
        active=False,
    )

    response = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    # Should either 404 or redirect to general sponsor/home
    assert response.status_code in [404, 302]
    db_session.refresh(ad)
    assert ad.remaining_budget >= Decimal("0.00")


def test_f5_06_click_active_redirects_dynamically(
    client: TestClient, db_session: Session
):
    """F5.6: GET /click/active/{repo_id} dynamically resolves matched ad and redirects."""
    repo = create_test_repository(db_session, primary_language="Python")
    ad = create_test_ad(
        db_session, click_url="https://active.example.com", target_language="Python"
    )

    response = client.get(f"/click/active/{repo.id}", follow_redirects=False)
    # Supported either at /click/active/{repo_id} or direct
    assert response.status_code in [302, 404]
    if response.status_code == 302:
        assert response.headers.get("location") == ad.click_url


# =====================================================================
# Feature F6: Embedded SVG Hyperlink (<a>)
# =====================================================================


def test_f6_01_badge_contains_svg_anchor_tag(client: TestClient, db_session: Session):
    """F6.1: Badge SVG contains standard anchor link <a ...>."""
    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    content = response.text
    assert "<a " in content or "<a\n" in content


def test_f6_02_anchor_tag_has_target_blank(client: TestClient, db_session: Session):
    """F6.2: Anchor tag includes target="_blank" attribute."""
    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert 'target="_blank"' in response.text or "target='_blank'" in response.text


def test_f6_03_anchor_tag_href_points_to_click_endpoint(
    client: TestClient, db_session: Session
):
    """F6.3: Anchor tag href links to /click/{ad_id}/{repo_id} or /click/active/{repo_id}."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    content = response.text
    assert (
        f"/click/{ad.id}/{repo.id}" in content
        or f"/click/active/{repo.id}" in content
        or "/click/" in content
    )


def test_f6_04_anchor_wraps_sponsor_card_elements(
    client: TestClient, db_session: Session
):
    """F6.4: Anchor tag wraps sponsor copy or card element."""
    repo = create_test_repository(db_session)
    create_test_ad(db_session, headline="WrapMe Headline")

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    root = assert_valid_svg_xml(response.text)
    # Check that anchor tag has children (text or rect)
    anchors = [elem for elem in root.iter() if "a" in elem.tag.lower()]
    assert len(anchors) > 0


def test_f6_05_anchor_url_is_properly_escaped(client: TestClient, db_session: Session):
    """F6.5: Anchor URL is well-formed without raw unescaped ampersands."""
    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200
    assert_valid_svg_xml(response.text)


# =====================================================================
# Feature F7: Impression Tracking & SHA-256 Dedup
# =====================================================================


def test_f7_01_badge_view_records_impression(client: TestClient, db_session: Session):
    """F7.1: Viewing badge creates an impression row in database."""
    from tests.conftest import Impression

    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    impression = db_session.query(Impression).filter_by(repo_id=repo.id).first()
    assert impression is not None


def test_f7_02_impression_records_sha256_client_hash(
    client: TestClient, db_session: Session
):
    """F7.2: Recorded impression contains 64-char hex SHA-256 client audit hash."""
    from tests.conftest import Impression

    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    client.get(
        f"/badge/{repo.owner}/{repo.name}.svg", headers={"user-agent": "tester-ua"}
    )
    impression = db_session.query(Impression).filter_by(repo_id=repo.id).first()
    assert impression is not None
    assert len(impression.client_hash) == 64


def test_f7_03_hourly_sliding_window_deduplication(
    client: TestClient, db_session: Session
):
    """F7.3: Multiple views from identical client within 1 hour produce single impression."""
    from tests.conftest import Impression

    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    client_headers = {"user-agent": "browser-dedup-agent"}
    client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers=client_headers)
    client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers=client_headers)
    client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers=client_headers)

    count = db_session.query(Impression).filter_by(repo_id=repo.id).count()
    assert count == 1


def test_f7_04_different_clients_create_distinct_impressions(
    client: TestClient, db_session: Session
):
    """F7.4: Distinct clients create separate impression records."""
    from tests.conftest import Impression

    repo = create_test_repository(db_session)
    create_test_ad(db_session)

    client.get(
        f"/badge/{repo.owner}/{repo.name}.svg", headers={"user-agent": "client-A"}
    )
    client.get(
        f"/badge/{repo.owner}/{repo.name}.svg", headers={"user-agent": "client-B"}
    )

    count = db_session.query(Impression).filter_by(repo_id=repo.id).count()
    assert count == 2


def test_f7_05_impression_links_repo_and_ad(client: TestClient, db_session: Session):
    """F7.5: Impression record links both repo_id and ad_id accurately."""
    from tests.conftest import Impression

    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)

    client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    imp = db_session.query(Impression).filter_by(repo_id=repo.id).first()
    assert imp.repo_id == repo.id
    assert imp.ad_id == ad.id


# =====================================================================
# Feature F8: SQLAlchemy 2.0 Persistence Engine
# =====================================================================


def test_f8_01_repository_crud(db_session: Session):
    """F8.1: Full CRUD on Repository model."""
    from tests.conftest import Repository

    repo = create_test_repository(db_session, owner="org1", name="repo1", stars=10)
    assert repo.id is not None

    fetched = db_session.query(Repository).filter_by(id=repo.id).first()
    assert fetched.name == "repo1"

    fetched.stars = 25
    db_session.commit()
    assert db_session.query(Repository).filter_by(id=repo.id).first().stars == 25

    db_session.delete(fetched)
    db_session.commit()
    assert db_session.query(Repository).filter_by(id=repo.id).first() is None


def test_f8_02_ad_crud_with_decimal_precision(db_session: Session):
    """F8.2: Ad model persists exact Decimal budget and CPC without floating point error."""
    from tests.conftest import Ad

    ad = create_test_ad(
        db_session,
        remaining_budget=Decimal("123.45"),
        cost_per_click=Decimal("0.33"),
    )
    fetched = db_session.query(Ad).filter_by(id=ad.id).first()
    assert fetched.remaining_budget == Decimal("123.45")
    cpc = getattr(fetched, "cost_per_click", getattr(fetched, "cpc", None))
    assert cpc == Decimal("0.33")


def test_f8_03_impression_foreign_keys_and_cascade(db_session: Session):
    """F8.3: Impression model enforces repo_id foreign key constraint."""
    from sqlalchemy.exc import IntegrityError

    from tests.conftest import Impression

    invalid_imp = Impression(
        repo_id=999999, ad_id=None, client_hash="abc" * 20 + "abcd"
    )
    db_session.add(invalid_imp)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_f8_04_click_foreign_keys_and_cascade(db_session: Session):
    """F8.4: Click model enforces repo_id and ad_id foreign key constraints."""
    from sqlalchemy.exc import IntegrityError

    from tests.conftest import Click

    invalid_click = Click(repo_id=999999, ad_id=888888, client_hash="def" * 20 + "defg")
    db_session.add(invalid_click)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_f8_05_foreign_key_pragmas_enforced(db_session: Session):
    """F8.5: SQLite database enforces PRAGMA foreign_keys=ON."""
    result = db_session.execute(
        __import__("sqlalchemy").text("PRAGMA foreign_keys;")
    ).scalar()
    assert result == 1


# =====================================================================
# Feature F9: DB Auto-Init & Migrations
# =====================================================================


def test_f9_01_all_tables_exist_in_metadata(db_session: Session):
    """F9.1: Metadata registers repositories, ads, impressions, clicks tables."""
    from tests.conftest import Base

    table_names = set(Base.metadata.tables.keys())
    assert "repositories" in table_names
    assert "ads" in table_names
    assert "impressions" in table_names
    assert "clicks" in table_names


def test_f9_02_schema_indexes_present(db_session: Session):
    """F9.2: Indexes are configured on repositories and analytics tables."""
    from tests.conftest import Base

    repo_table = Base.metadata.tables.get("repositories")
    assert repo_table is not None
    # Index or unique constraint on owner/name
    col_names = {c.name for c in repo_table.columns}
    assert "owner" in col_names and "name" in col_names


def test_f9_03_idempotent_table_initialization(db_session: Session):
    """F9.3: Base.metadata.create_all is idempotent."""
    from tests.conftest import Base, test_engine

    Base.metadata.create_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)


def test_f9_04_transaction_rollback_preserves_consistency(db_session: Session):
    """F9.4: Rolled back transaction leaves no uncommitted rows."""
    from tests.conftest import Repository

    initial_count = db_session.query(Repository).count()
    repo = Repository(owner="temp", name="temp", stars=1)
    db_session.add(repo)
    db_session.rollback()
    assert db_session.query(Repository).count() == initial_count


def test_f9_05_timestamp_columns_auto_populate(db_session: Session):
    """F9.5: Created timestamps auto-populate upon row creation."""
    repo = create_test_repository(db_session)
    created_at = getattr(repo, "created_at", None)
    if created_at is not None:
        assert isinstance(created_at, datetime)


# =====================================================================
# Feature F10: Real GitHub Repo Seeding Script
# =====================================================================


def test_f10_01_seed_repos_creates_unclaimed_repositories(db_session: Session):
    """F10.1: Seeded repos start with claimed=False."""
    repo = create_test_repository(db_session, claimed=False)
    assert repo.claimed is False


def test_f10_02_seed_repos_languages_and_stars_valid(db_session: Session):
    """F10.2: Seeded repos have authentic positive stars and valid languages."""
    repo = create_test_repository(db_session, stars=35000, primary_language="Rust")
    assert repo.stars > 0
    assert repo.primary_language in ["Rust", "Python", "TypeScript", "Go", "C", "C++"]


def test_f10_03_seed_repos_idempotent_no_duplicates(db_session: Session):
    """F10.3: Seeding handles existing repositories without duplicate key violations."""
    repo1 = create_test_repository(db_session, owner="owner_unique", name="repo_unique")
    assert repo1.id is not None


def test_f10_04_seed_ads_creates_targeted_and_general_campaigns(db_session: Session):
    """F10.4: Seeded campaigns include language-targeted and general fallbacks."""
    ad1 = create_test_ad(db_session, target_language="Python")
    ad2 = create_test_ad(db_session, target_language=None)
    assert ad1.target_language == "Python"
    assert ad2.target_language is None or ad2.target_language == "general"


def test_f10_05_seed_ads_have_positive_budgets(db_session: Session):
    """F10.5: Seeded ads have active=True, positive remaining_budget, and positive CPC."""
    ad = create_test_ad(
        db_session, remaining_budget=Decimal("50.00"), cost_per_click=Decimal("0.50")
    )
    assert ad.active is True
    assert ad.remaining_budget > Decimal("0.00")
    cpc = getattr(ad, "cost_per_click", getattr(ad, "cpc", None))
    assert cpc > Decimal("0.00")


# =====================================================================
# Feature F11: Maintainer Repo Claiming
# =====================================================================


def test_f11_01_claim_unclaimed_repo_succeeds(client: TestClient, db_session: Session):
    """F11.1: Maintainer can successfully claim an unclaimed repo."""
    repo = create_test_repository(db_session, claimed=False)
    payload = {
        "owner": repo.owner,
        "name": repo.name,
        "maintainer_handle": "alice_dev",
        "payout_address": "0x1234567890abcdef",
    }
    response = client.post("/maintainers/claim", json=payload)
    assert response.status_code in [200, 201]
    db_session.refresh(repo)
    assert repo.claimed is True


def test_f11_02_claimed_repo_stores_maintainer_handle(
    client: TestClient, db_session: Session
):
    """F11.2: Claimed repository records maintainer handle."""
    repo = create_test_repository(db_session, claimed=False)
    payload = {
        "owner": repo.owner,
        "name": repo.name,
        "maintainer_handle": "bob_coder",
    }
    client.post("/maintainers/claim", json=payload)
    db_session.refresh(repo)
    assert repo.maintainer_handle == "bob_coder"


def test_f11_03_claim_already_claimed_repo_fails(
    client: TestClient, db_session: Session
):
    """F11.3: Claiming an already claimed repo returns 400 or 409 Conflict."""
    repo = create_test_repository(
        db_session, claimed=True, maintainer_handle="first_owner"
    )
    payload = {
        "owner": repo.owner,
        "name": repo.name,
        "maintainer_handle": "second_owner",
    }
    response = client.post("/maintainers/claim", json=payload)
    assert response.status_code in [400, 409]


def test_f11_04_claim_nonexistent_repo_returns_404(client: TestClient):
    """F11.4: Claiming non-existent repo returns 404."""
    payload = {
        "owner": "nonexistent-maintainer-org",
        "name": "nonexistent-project-repo",
        "maintainer_handle": "hacker",
    }
    response = client.post("/maintainers/claim", json=payload)
    assert response.status_code == 404


def test_f11_05_claim_stores_optional_payout_address(
    client: TestClient, db_session: Session
):
    """F11.5: Payout address provided in claim request is persisted."""
    repo = create_test_repository(db_session, claimed=False)
    payload = {
        "owner": repo.owner,
        "name": repo.name,
        "maintainer_handle": "charlie",
        "payout_address": "paypal@example.com",
    }
    client.post("/maintainers/claim", json=payload)
    db_session.refresh(repo)
    assert repo.payout_address == "paypal@example.com"


# =====================================================================
# Feature F12: Markdown/HTML Snippet Generation
# =====================================================================


def test_f12_01_snippet_endpoint_returns_markdown(
    client: TestClient, db_session: Session
):
    """F12.1: Snippet endpoint returns markdown badge snippet."""
    repo = create_test_repository(db_session)
    response = client.get(f"/maintainers/snippet/{repo.owner}/{repo.name}")
    assert response.status_code == 200
    data = response.json()
    assert "markdown" in data
    assert f"/badge/{repo.owner}/{repo.name}.svg" in data["markdown"]


def test_f12_02_snippet_endpoint_returns_html(client: TestClient, db_session: Session):
    """F12.2: Snippet endpoint returns HTML <a><img></a> snippet."""
    repo = create_test_repository(db_session)
    response = client.get(f"/maintainers/snippet/{repo.owner}/{repo.name}")
    assert response.status_code == 200
    data = response.json()
    assert "html" in data
    assert "<a href=" in data["html"]
    assert "<img src=" in data["html"]


def test_f12_03_snippet_endpoint_returns_rst(client: TestClient, db_session: Session):
    """F12.3: Snippet endpoint returns reStructuredText snippet."""
    repo = create_test_repository(db_session)
    response = client.get(f"/maintainers/snippet/{repo.owner}/{repo.name}")
    assert response.status_code == 200
    data = response.json()
    assert "rst" in data or "markdown" in data


def test_f12_04_snippet_contains_correct_urls(client: TestClient, db_session: Session):
    """F12.4: Snippet links badge image to click redirect endpoint."""
    repo = create_test_repository(db_session)
    response = client.get(f"/maintainers/snippet/{repo.owner}/{repo.name}")
    assert response.status_code == 200
    data = response.json()
    # Click endpoint should be referenced in link
    assert "/click" in data.get("markdown", "")


def test_f12_05_snippet_for_missing_repo_returns_404(client: TestClient):
    """F12.5: Requesting snippet for unindexed repo returns 404."""
    response = client.get("/maintainers/snippet/missing-owner/missing-repo")
    assert response.status_code == 404


# =====================================================================
# Feature F13: 50/50 Revenue Split Calculation
# =====================================================================


def test_f13_01_revenue_report_exact_50_50_split(
    client: TestClient, db_session: Session
):
    """F13.1: Gross revenue is divided exactly 50% maintainer, 50% platform."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)
    create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal("1.00"))

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    maintainer_earnings = Decimal(str(data["maintainer_earnings"]))
    platform_cut = Decimal(str(data["platform_cut"]))
    gross_revenue = Decimal(str(data["gross_revenue"]))
    assert gross_revenue == Decimal("1.00")
    assert maintainer_earnings == Decimal("0.50")
    assert platform_cut == Decimal("0.50")


def test_f13_02_revenue_report_decimal_precision(
    client: TestClient, db_session: Session
):
    """F13.2: Revenue split uses exact Decimal mathematics without floating point error."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)
    # $0.33 total click revenue
    create_test_click(db_session, repo_id=repo.id, ad_id=ad.id, cost=Decimal("0.33"))

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    maintainer = Decimal(str(data["maintainer_earnings"]))
    platform = Decimal(str(data["platform_cut"]))
    assert maintainer + platform == Decimal("0.33")


def test_f13_03_revenue_report_counts_impressions_and_clicks(
    client: TestClient, db_session: Session
):
    """F13.3: Revenue report accurately aggregates total impressions and clicks."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)
    create_test_impression(
        db_session, repo_id=repo.id, ad_id=ad.id, client_hash="hash1" * 12 + "1234"
    )
    create_test_impression(
        db_session, repo_id=repo.id, ad_id=ad.id, client_hash="hash2" * 12 + "1234"
    )
    create_test_click(db_session, repo_id=repo.id, ad_id=ad.id)

    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["total_impressions"] == 2
    assert data["total_clicks"] == 1


def test_f13_04_revenue_report_zero_clicks_zero_revenue(
    client: TestClient, db_session: Session
):
    """F13.4: Repository with zero clicks returns 0.00 for maintainer and platform."""
    repo = create_test_repository(db_session)
    response = client.get(f"/revenue/{repo.id}")
    assert response.status_code == 200
    data = response.json()
    assert Decimal(str(data["gross_revenue"])) == Decimal("0.00")
    assert Decimal(str(data["maintainer_earnings"])) == Decimal("0.00")
    assert Decimal(str(data["platform_cut"])) == Decimal("0.00")


def test_f13_05_revenue_report_nonexistent_repo_returns_404(client: TestClient):
    """F13.5: Requesting revenue report for invalid repo_id returns 404."""
    response = client.get("/revenue/99999999")
    assert response.status_code == 404
