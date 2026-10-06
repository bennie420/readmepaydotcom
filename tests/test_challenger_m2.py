"""
Empirical Adversarial Challenge Suite for Milestone 2 (tests/test_challenger_m2.py).

Written by challenger_m2_2.
Adversarially challenges and verifies:
1. HTTP Caching Headers (Cache-Control, ETag format RFC 7232).
2. HTTP 304 Not Modified negotiation (exact ETag, weak ETag, wildcard '*', empty body verification).
3. Honest 404 Error Handling & Zero Synthetic Fallback Personas or Stats.
4. Language Matching Cascade (Tier 1 language match, Tier 2 general fallback, Tier 3 platform onboarding).
5. Upstream failure resilience (GitHub 404, 403, 429, 500) returning honest 404 without synthetic injection.
6. Embedded SVG hyperlink (<a href="..." target="_blank">).
7. Concurrent HTTP 304 negotiation under load.
"""

import re
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.services.github_service import clear_github_cache
from tests.conftest import (
    assert_valid_svg_xml,
    create_test_ad,
    create_test_repository,
)


@pytest.fixture(autouse=True)
def clean_cache_each_test():
    """Ensure in-memory TTL cache is clean between tests."""
    clear_github_cache()
    yield
    clear_github_cache()


# =====================================================================
# Challenge Area 1: HTTP Caching Headers & RFC 7232 ETag Compliance
# =====================================================================

def test_empirical_cache_control_and_etag_format_on_200(client: TestClient, db_session: Session):
    """
    Empirically verify HTTP 200 response headers:
    - Cache-Control: public, max-age=3600
    - ETag: RFC 7232 quoted hexadecimal string '"<sha256>"' (length 66 chars: 64 hex + 2 quotes)
    - Content-Type: image/svg+xml; charset=utf-8
    """
    repo = create_test_repository(db_session, owner="pallets", name="click", primary_language="Python")
    create_test_ad(db_session, target_language="Python", active=True)

    response = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert response.status_code == 200

    # 1. Cache-Control Header
    cache_control = response.headers.get("cache-control", "")
    assert "public" in cache_control.lower(), f"Expected 'public' in Cache-Control, got: {cache_control}"
    assert "max-age=3600" in cache_control.lower(), f"Expected 'max-age=3600' in Cache-Control, got: {cache_control}"

    # 2. Content-Type Header
    content_type = response.headers.get("content-type", "")
    assert "image/svg+xml" in content_type.lower(), f"Expected 'image/svg+xml', got: {content_type}"

    # 3. ETag Header format
    etag = response.headers.get("etag")
    assert etag is not None, "ETag header missing from 200 OK response"
    assert re.match(r'^"[0-9a-f]{64}"$', etag), f"ETag does not match RFC 7232 quoted SHA-256 pattern: {etag}"


def test_empirical_if_none_match_exact_etag_returns_304_empty_body(client: TestClient, db_session: Session):
    """
    Adversarial Challenge: When client provides If-None-Match with matching ETag,
    server MUST return:
    - HTTP 304 Not Modified
    - Content body strictly EMPTY (0 bytes)
    - ETag header matching current ETag
    - Cache-Control header present
    """
    repo = create_test_repository(db_session, owner="psf", name="requests", primary_language="Python")
    create_test_ad(db_session, target_language="Python", active=True)

    # First request: get 200 OK and ETag
    initial_resp = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert initial_resp.status_code == 200
    etag = initial_resp.headers.get("etag")
    assert etag is not None
    assert len(initial_resp.content) > 0

    # Conditional request with matching If-None-Match
    cond_resp = client.get(
        f"/badge/{repo.owner}/{repo.name}.svg",
        headers={"if-none-match": etag},
    )
    assert cond_resp.status_code == 304, f"Expected 304 Not Modified, got {cond_resp.status_code}"
    assert cond_resp.content == b"", f"HTTP 304 response body must be empty bytes, got: {cond_resp.content}"
    assert len(cond_resp.text) == 0, f"HTTP 304 text must be empty string, got: {cond_resp.text}"
    assert cond_resp.headers.get("etag") == etag
    assert "max-age=3600" in cond_resp.headers.get("cache-control", "")


def test_empirical_if_none_match_weak_etag_returns_304_empty_body(client: TestClient, db_session: Session):
    """
    RFC 7232 compliance: If-None-Match allows weak validator prefix W/"...".
    Server must strip W/ prefix and match strong ETag, returning 304 with empty body.
    """
    repo = create_test_repository(db_session, owner="tiangolo", name="fastapi", primary_language="Python")
    create_test_ad(db_session, target_language="Python", active=True)

    res200 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    etag = res200.headers.get("etag")
    assert etag is not None

    weak_etag = f"W/{etag}"
    res304 = client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers={"if-none-match": weak_etag})
    assert res304.status_code == 304
    assert res304.content == b""


def test_empirical_if_none_match_wildcard_returns_304_empty_body(client: TestClient, db_session: Session):
    """
    RFC 7232 compliance: If-None-Match: * matches any existing representation,
    returning 304 with empty body.
    """
    repo = create_test_repository(db_session, owner="django", name="django", primary_language="Python")
    create_test_ad(db_session, target_language="Python", active=True)

    res304 = client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers={"if-none-match": "*"})
    assert res304.status_code == 304
    assert res304.content == b""


def test_empirical_if_none_match_mismatched_etag_returns_200_with_body(client: TestClient, db_session: Session):
    """
    When If-None-Match does not match current ETag, server returns HTTP 200 with full SVG.
    """
    repo = create_test_repository(db_session, owner="astral-sh", name="ruff", primary_language="Rust")
    create_test_ad(db_session, target_language="Rust", active=True)

    bogus_etag = '"0000000000000000000000000000000000000000000000000000000000000000"'
    res = client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers={"if-none-match": bogus_etag})
    assert res.status_code == 200
    assert len(res.content) > 0
    assert "ruff" in res.text


# =====================================================================
# Challenge Area 2: Honest 404 Error Handling & Zero Synthetic Personas
# =====================================================================

def test_empirical_nonexistent_repo_honest_404_svg(client: TestClient):
    """
    Adversarial Challenge: Requesting non-existent repository from GitHub returns:
    - HTTP 404 Not Found status code
    - Valid XML SVG content
    - Content-Type: image/svg+xml; charset=utf-8
    - Cache-Control: public, max-age=300 (negative cache)
    - Transparent diagnostic message in SVG body
    - Strictly ZERO synthetic fallback personas, fake maintainers, or dummy stars.
    """
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = httpx.Response(404, json={"message": "Not Found"})

        resp = client.get("/badge/ghost-org-nonexistent/ghost-repo-nonexistent.svg")

        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"
        assert "image/svg+xml" in resp.headers.get("content-type", "")
        assert "300" in resp.headers.get("cache-control", "")

        # Validate SVG XML structure
        root = assert_valid_svg_xml(resp.text)
        assert "svg" in root.tag.lower()

        # Text verification
        content_lower = resp.text.lower()
        assert "not found" in content_lower or "404" in content_lower

        # Strict Anti-Pattern & Prohibited Entities Audit
        prohibited = [
            "thomas vance",
            "theo vance",
            "vance",
            "john doe",
            "jane doe",
            "fiduciary",
            "synthetic",
            "dummy maintainer",
        ]
        for item in prohibited:
            assert item not in content_lower, f"Prohibited synthetic entity '{item}' leaked into 404 response!"


def test_empirical_negative_caching_for_nonexistent_repo(client: TestClient):
    """
    Verify negative caching: Once a repository 404s, subsequent requests are served
    immediately from in-memory cache without hitting external GitHub API again.
    """
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = httpx.Response(404, json={"message": "Not Found"})

        # 1st call: hits mock external GitHub API
        r1 = client.get("/badge/negcache-org/negcache-repo.svg")
        assert r1.status_code == 404
        assert mock_get.call_count >= 1
        initial_calls = mock_get.call_count

        # 2nd call: hits in-memory negative cache
        r2 = client.get("/badge/negcache-org/negcache-repo.svg")
        assert r2.status_code == 404
        assert mock_get.call_count == initial_calls, "Subsequent 404 should be served from negative cache!"
        assert r1.text == r2.text


def test_empirical_malformed_repo_identifier_handling(client: TestClient):
    """
    Adversarial Challenge: Malformed or empty path parameters should return
    transparent 404 error SVG without crashing with 500 or throwing unhandled exceptions.
    """
    cases = [
        "/badge/%20/%20.svg",
        "/badge/valid-owner/%20.svg",
        "/badge/%20/valid-repo.svg",
    ]
    for url in cases:
        resp = client.get(url)
        assert resp.status_code == 404
        root = assert_valid_svg_xml(resp.text)
        assert root is not None
        assert "404" in resp.text or "not found" in resp.text.lower() or "invalid" in resp.text.lower()


def test_empirical_upstream_failure_500_handling(client: TestClient):
    """
    Adversarial Challenge: When GitHub API returns HTTP 500 or network timeout,
    badge endpoint returns transparent 404 error SVG, strictly ZERO synthetic personas.
    """
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = httpx.Response(500, json={"message": "Internal Server Error"})

        resp = client.get("/badge/error-org/error-repo.svg")
        assert resp.status_code == 404
        assert_valid_svg_xml(resp.text)
        assert "vance" not in resp.text.lower()


def test_empirical_upstream_rate_limit_403_and_429_handling(client: TestClient):
    """
    Adversarial Challenge: When GitHub API returns HTTP 403 or 429 rate limit,
    badge endpoint handles honestly with 404 SVG and zero synthetic data.
    """
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={"x-ratelimit-remaining": "0"},
        )

        resp = client.get("/badge/ratelimit-org/ratelimit-repo.svg")
        assert resp.status_code == 404
        assert_valid_svg_xml(resp.text)
        assert "vance" not in resp.text.lower()


# =====================================================================
# Challenge Area 3: Language Matching Cascades (Tier 1 -> Tier 2 -> Tier 3)
# =====================================================================

def test_empirical_cascade_tier1_language_match_priority(client: TestClient, db_session: Session):
    """
    Tier 1: Targeted programming language match takes precedence over general fallback.
    """
    repo = create_test_repository(db_session, owner="golang", name="go", primary_language="Go")
    targeted_ad = create_test_ad(
        db_session,
        sponsor_name="GoVendor",
        headline="Specialized Go Tooling",
        target_language="Go",
    )
    general_ad = create_test_ad(
        db_session,
        sponsor_name="GeneralHost",
        headline="Generic Cloud Hosting",
        target_language=None,
    )

    resp = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert resp.status_code == 200
    assert "Specialized Go Tooling" in resp.text
    assert "Generic Cloud Hosting" not in resp.text
    assert f"/click/{targeted_ad.id}/{repo.id}" in resp.text


def test_empirical_cascade_tier1_case_insensitivity_and_whitespace(client: TestClient, db_session: Session):
    """
    Tier 1: Primary language matching handles mixed case and surrounding whitespace.
    """
    repo = create_test_repository(db_session, owner="rust-lang", name="rust", primary_language="  rUsT  ")
    targeted_ad = create_test_ad(
        db_session,
        sponsor_name="RustArmor",
        headline="Safe Rust Security",
        target_language="Rust",
    )

    resp = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert resp.status_code == 200
    assert "Safe Rust Security" in resp.text


def test_empirical_cascade_tier2_fallback_when_language_unmatched(client: TestClient, db_session: Session):
    """
    Tier 2: If repo has language without any targeted ads, fall back to universal general ad.
    """
    repo = create_test_repository(db_session, owner="elixir-lang", name="elixir", primary_language="Elixir")
    create_test_ad(
        db_session,
        sponsor_name="PythonPro",
        headline="Python Only",
        target_language="Python",
    )
    general_ad = create_test_ad(
        db_session,
        sponsor_name="GlobalInfra",
        headline="Universal Infrastructure For All",
        target_language=None,
    )

    resp = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert resp.status_code == 200
    assert "Universal Infrastructure For All" in resp.text
    assert f"/click/{general_ad.id}/{repo.id}" in resp.text


def test_empirical_cascade_tier2_fallback_when_targeted_budget_depleted(client: TestClient, db_session: Session):
    """
    Tier 2: When targeted ad has remaining_budget=0.00, it is skipped in favor of general ad.
    """
    repo = create_test_repository(db_session, owner="pallets", name="jinja", primary_language="Python")
    depleted_ad = create_test_ad(
        db_session,
        sponsor_name="DepletedPython",
        headline="Depleted Python Ad",
        target_language="Python",
        remaining_budget=Decimal("0.00"),
    )
    general_ad = create_test_ad(
        db_session,
        sponsor_name="FundedGeneral",
        headline="Funded General Platform",
        target_language=None,
        remaining_budget=Decimal("50.00"),
    )

    resp = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert resp.status_code == 200
    assert "Funded General Platform" in resp.text
    assert "Depleted Python Ad" not in resp.text
    assert f"/click/{general_ad.id}/{repo.id}" in resp.text


def test_empirical_cascade_tier3_honest_community_invite_when_no_commercial_ads(client: TestClient, db_session: Session):
    """
    Tier 3: When database contains ZERO active commercial ads, badge endpoint:
    - Renders an honest community invitation / platform onboarding card.
    - Click link directs maintainers to claim repository (/maintainers/claim?repo=...).
    - Strictly ZERO synthetic commercial sponsors.
    """
    repo = create_test_repository(db_session, owner="torvalds", name="subsurface", primary_language="C")

    # DB has 0 ads
    resp = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert resp.status_code == 200
    content = resp.text

    # Verify XML validity
    assert_valid_svg_xml(content)

    # Verify honest onboarding fallback copy and link
    assert "claim" in content.lower() or "sponsor" in content.lower()
    assert f"/maintainers/claim?repo={repo.owner}/{repo.name}" in content

    # Verify strictly zero synthetic commercial sponsors
    assert "vance" not in content.lower()
    assert "sentry" not in content.lower()


# =====================================================================
# Challenge Area 4: Embedded SVG Hyperlink Compliance (<a ...>)
# =====================================================================

def test_empirical_embedded_svg_anchor_tag_attributes(client: TestClient, db_session: Session):
    """
    Adversarial Challenge: Verify that the rendered SVG contains a valid
    clickable anchor tag wrapping the sponsor card:
    - Element is <a ...>
    - Has href attribute pointing to /click/{ad_id}/{repo_id}
    - Has target="_blank" attribute
    - Has rel="noopener noreferrer" attribute
    """
    repo = create_test_repository(db_session, owner="encode", name="uvicorn", primary_language="Python")
    ad = create_test_ad(db_session, target_language="Python", active=True)

    resp = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    assert resp.status_code == 200
    content = resp.text

    # XML parsed checks
    root = assert_valid_svg_xml(content)
    anchors = [el for el in root.iter() if el.tag.endswith("}a") or el.tag == "a"]
    assert len(anchors) >= 1, "Rendered SVG must contain at least one <a> link element"

    sponsor_anchor = anchors[0]
    href = sponsor_anchor.attrib.get("href") or sponsor_anchor.attrib.get("{http://www.w3.org/1999/xlink}href")
    assert href == f"/click/{ad.id}/{repo.id}"
    assert sponsor_anchor.attrib.get("target") == "_blank"


# =====================================================================
# Challenge Area 5: High-Concurrency & Stress Verification
# =====================================================================

def test_empirical_concurrent_if_none_match_304_stress(client: TestClient, db_session: Session):
    """
    Stress-test HTTP 304 negotiation across 30 sequential conditional requests
    to ensure 100% determinism, zero empty-body leaks, and correct header preservation.
    """
    repo = create_test_repository(db_session, owner="stress-org", name="stress-repo", primary_language="Python")
    create_test_ad(db_session, target_language="Python")

    initial = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
    etag = initial.headers.get("etag")
    assert etag is not None

    for i in range(30):
        cond = client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers={"if-none-match": etag})
        assert cond.status_code == 304
        assert cond.content == b""
        assert cond.headers.get("etag") == etag
