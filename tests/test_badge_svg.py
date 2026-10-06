"""Unit & Integration Tests for Badge SVG Rendering and HTTP Headers."""

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.services.badge_service import (
    build_badge_svg,
    build_error_svg,
    compute_svg_etag,
    format_stars,
    get_language_color,
    truncate_text,
)
from tests.conftest import (
    assert_valid_svg_xml,
    create_test_ad,
    create_test_repository,
)


def test_badge_returns_200_and_svg_mime(client: TestClient, db_session: Session):
    """Valid badge request returns HTTP 200 with image/svg+xml content-type."""
    repo = create_test_repository(db_session, owner="pallets", name="flask", stars=68000, primary_language="Python")
    create_test_ad(db_session, target_language="Python")

    resp = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert resp.status_code == 200
    assert "image/svg+xml" in resp.headers.get("content-type", "")


def test_badge_valid_xml_and_viewbox(client: TestClient, db_session: Session):
    """Badge compiles to valid XML root <svg> with responsive viewBox 0 0 500 110."""
    repo = create_test_repository(db_session, owner="fastapi", name="fastapi")
    create_test_ad(db_session)

    resp = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert resp.status_code == 200
    root = assert_valid_svg_xml(resp.text)
    assert "svg" in root.tag.lower()
    viewbox = root.attrib.get("viewBox", "")
    assert "0 0 500 110" in viewbox or ("500" in viewbox and "110" in viewbox)


def test_badge_star_formatting_scales(client: TestClient, db_session: Session):
    """Stars count is formatted cleanly across ranges: 0, 1200, 68000, 1500000."""
    cases = [
        (0, ["0"]),
        (1250, ["1.2k", "1.3k", "1250"]),
        (68000, ["68k", "68K", "68000"]),
        (1500000, ["1.5M", "1.5m", "1500000"]),
    ]
    for stars, expected_formats in cases:
        repo = create_test_repository(db_session, owner="scale", name=f"repo-{stars}", stars=stars)
        create_test_ad(db_session)
        resp = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
        assert resp.status_code == 200
        assert any(exp in resp.text for exp in expected_formats)


def test_badge_cache_control_and_etag_headers(client: TestClient, db_session: Session):
    """Badge response includes public Cache-Control max-age=3600 and valid ETag header."""
    repo = create_test_repository(db_session, owner="org", name="cached-repo")
    create_test_ad(db_session)

    resp = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert resp.status_code == 200
    cache_header = resp.headers.get("cache-control", "").lower()
    assert "public" in cache_header and "3600" in cache_header
    assert "etag" in resp.headers
    assert len(resp.headers["etag"]) > 0


def test_conditional_request_304_not_modified(client: TestClient, db_session: Session):
    """Request with matching If-None-Match header returns HTTP 304 Not Modified."""
    repo = create_test_repository(db_session, owner="org", name="etag-repo")
    create_test_ad(db_session)

    resp1 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert resp1.status_code == 200
    etag = resp1.headers.get("etag")
    assert etag is not None

    resp2 = client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers={"if-none-match": etag})
    assert resp2.status_code == 304
    assert resp2.text == ""


def test_embedded_svg_hyperlink_target_blank(client: TestClient, db_session: Session):
    """Badge SVG contains standard anchor element linking to click endpoint with target="_blank"."""
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)

    resp = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert resp.status_code == 200
    content = resp.text
    assert "<a " in content
    assert 'target="_blank"' in content
    assert f"/click/{ad.id}/{repo.id}" in content or f"/click/active/{repo.id}" in content


def test_nonexistent_repo_honest_404_or_error_svg(client: TestClient):
    """Nonexistent repository returns honest 404 or valid error SVG without synthetic personas."""
    resp = client.get("/badge/nonexistent-org-99/unregistered-repo-99.svg")
    assert resp.status_code in [200, 404]
    content = resp.text.lower()
    if resp.status_code == 200 or "svg" in content:
        assert_valid_svg_xml(resp.text)
        assert "not found" in content or "error" in content or "404" in content
    # Strict anti-pattern checks
    assert "thomas vance" not in content
    assert "theo vance" not in content


def test_badge_service_unit_methods():
    """Unit test individual badge_service methods."""
    assert format_stars(0) == "0"
    assert format_stars(950) == "950"
    assert format_stars(1200) == "1.2k"
    assert format_stars(51000) == "51k"
    assert format_stars(1500000) == "1.5M"

    assert get_language_color("python") == "#3572A5"
    assert get_language_color("Rust") == "#dea584"
    assert get_language_color(None) == "#8b949e"

    assert truncate_text("short", 10) == "short"
    assert truncate_text("a" * 20, 10).endswith("...")

    # Build badge directly
    svg = build_badge_svg(
        repo_name="my-repo",
        stars=1000,
        language="Python",
        ci_status="passing",
    )
    assert_valid_svg_xml(svg)
    etag = compute_svg_etag(svg)
    assert etag.startswith('"') and etag.endswith('"')

    # Build error svg
    err_svg = build_error_svg("Test Error", "Test message", 404)
    assert_valid_svg_xml(err_svg)
    assert "404" in err_svg


def test_badge_chameleon_media_query(client: TestClient, db_session: Session):
    """Badge SVG contains embedded prefers-color-scheme media query for seamless dark/light GitHub rendering."""
    repo = create_test_repository(db_session, owner="adaptive", name="chameleon")
    create_test_ad(db_session)

    resp = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert resp.status_code == 200
    svg = resp.text
    assert "@media (prefers-color-scheme: light)" in svg
    assert ".outer-card" in svg
    assert ".ad-inner-card" in svg


def test_badge_shield_style(client: TestClient, db_session: Session):
    """Shield style query parameter returns compact 320x28 Shields.io style SVG."""
    repo = create_test_repository(db_session, owner="shields", name="compact-repo")
    ad = create_test_ad(db_session)

    resp = client.get(f"/badge/{repo.owner}/{repo.name}.svg?style=shield")
    assert resp.status_code == 200
    assert "image/svg+xml" in resp.headers["content-type"]
    svg = resp.text
    assert_valid_svg_xml(svg)
    assert "viewBox=\"0 0 320 28\"" in svg
    assert "SPONSOR" in svg
    assert ad.sponsor_name in svg

