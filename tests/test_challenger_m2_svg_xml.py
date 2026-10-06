"""
tests/test_challenger_m2_svg_xml.py - Empirical Adversarial Challenge Suite for Milestone 2 SVG & XML Security

Written by empirical challenger agent (challenger_m2_1).
Empirically stress-tests:
1. XML Injection and XSS Resistance (<script>, event handlers, unescaped quotes, raw ampersands).
2. Sanitization of dangerous URL schemes (javascript:, data:, vbscript:) in SVG hyperlinks.
3. Unicode / Multilingual Repository Names and Metadata (CJK, Cyrillic, RTL, Emojis).
4. Star Count Formatting Boundaries (0, extreme values, 1,500,000+, negative, float).
5. CI/CD Status Dimensions (None, passing, failing, pending, unknown, alignment).
6. SVG Spec & Responsive Dimensions (viewBox="0 0 500 110", width="500", height="110").
7. Hyperlink Extraction Oracle (SVG <a> element, target="_blank", rel="noopener").
8. RFC 7232 ETag Validation & HTTP 304 Negotiation (Strong & Weak ETags, wildcard *).
9. Honest Error Handling Integrity (404 Error SVG, zero Vance/synthetic data).
10. Multithreaded SVG Rendering Concurrency.
"""

from __future__ import annotations

import concurrent.futures
import re
import urllib.parse

import pytest
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.services.badge_service import (
    build_badge_svg,
    build_error_svg,
    compute_svg_etag,
    format_stars,
    get_ci_colors,
    sanitize_url,
    validate_svg_xml,
)
from tests.conftest import (
    assert_valid_svg_xml,
    create_test_ad,
    create_test_repository,
)

SVG_NS = "{http://www.w3.org/2000/svg}"
XLINK_NS = "{http://www.w3.org/1999/xlink}"


# =====================================================================
# 1. XML Injection and XSS Attack Payloads
# =====================================================================

class TestXmlInjectionAndSecurity:
    """Adversarial testing against XML injection, XSS vectors, and malformed entities."""

    MALICIOUS_PAYLOADS = [
        "<script>alert('XSS')</script>",
        "</text><script>alert(1)</script><text>",
        "test<script>&foo'bar\"",
        "'>\"><script>alert(1)</script>",
        "& < > \" ' &amp; &lt; &gt; &#34; &#39;",
        "<!--#exec cmd=\"cat /etc/passwd\"-->",
        "<![CDATA[<script>alert('cdata')</script>]]>",
        "<svg onload=\"alert('svg_xss')\">",
        "<image href=\"x\" onerror=\"alert('img_err')\"/>",
        "<a href=\"javascript:alert('link')\">click</a>",
        "Repo & AT&T & R&D & Partners",
        "\" onmouseover=\"alert('hover')\" x=\"",
        "';alert(String.fromCharCode(88,83,83))//\\';alert(1)//",
        "repo\twith\nnewlines\rand\ttabs",
    ]

    @pytest.mark.parametrize("payload", MALICIOUS_PAYLOADS)
    def test_xml_injection_in_repo_name(self, payload: str):
        """Repository name containing dangerous XML/XSS strings must be safely escaped."""
        svg = build_badge_svg(
            repo_name=payload,
            stars=100,
            language="Python",
            ci_status="passing",
        )
        # 1. Must parse as strictly valid XML
        root = validate_svg_xml(svg)
        assert root is not None
        # 2. Must not create unescaped script elements
        scripts = [elem for elem in root.iter() if "script" in elem.tag.lower()]
        assert len(scripts) == 0, f"Unescaped script element injected: {scripts}"
        # 3. Raw unescaped <script> tag must not appear in XML text
        assert "<script>" not in svg
        assert "</script>" not in svg

    @pytest.mark.parametrize("payload", MALICIOUS_PAYLOADS)
    def test_xml_injection_in_ad_metadata(self, payload: str):
        """Ad fields (sponsor_name, headline, cta_text) containing XML/XSS must be escaped."""
        ad_dict = {
            "sponsor_name": f"Sponsor: {payload}",
            "headline": f"Headline: {payload}",
            "call_to_action": f"CTA: {payload}",
            "click_url": f"https://example.com/ad?param={urllib.parse.quote(payload)}",
        }
        svg = build_badge_svg(
            repo_name="secure-repo",
            stars=5000,
            language="TypeScript",
            ci_status="passing",
            ad=ad_dict,
        )
        root = validate_svg_xml(svg)
        scripts = [elem for elem in root.iter() if "script" in elem.tag.lower()]
        assert len(scripts) == 0
        assert "<script>" not in svg

    @pytest.mark.parametrize("payload", MALICIOUS_PAYLOADS)
    def test_xml_injection_in_error_svg(self, payload: str):
        """Error SVG title and message containing XML/XSS must be safely escaped."""
        err_svg = build_error_svg(
            error_title=f"Error: {payload}",
            error_message=f"Details: {payload}",
            error_code=404,
        )
        root = validate_svg_xml(err_svg)
        scripts = [elem for elem in root.iter() if "script" in elem.tag.lower()]
        assert len(scripts) == 0
        assert "<script>" not in err_svg


# =====================================================================
# 2. URL Scheme Sanitization & Dangerous Link Neutralization
# =====================================================================

class TestUrlSchemeSanitization:
    """Stress-test sanitize_url against protocol evasion and JavaScript execution."""

    DANGEROUS_URLS = [
        "javascript:alert(1)",
        "JAVASCRIPT:alert(1)",
        "  javascript:alert('spaced')  ",
        "javascript:/*--></title></style></textarea></script></xmp><svg/onload=alert(1)>",
        "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
        "DATA:image/svg+xml;base64,PHN2Zz48c2NyaXB0PmFsZXJ0KDEpPC9zY3JpcHQ+PC9zdmc+",
        "vbscript:msgbox(1)",
        "VBSCRIPT:alert(1)",
    ]

    SAFE_URLS = [
        "/click/1/1",
        "/maintainers/claim?repo=pallets/flask",
        "https://example.com/sponsor",
        "http://sponsor.io/campaign?utm_source=badge&utm_medium=banner",
        "https://sponsor.com/path#pricing",
    ]

    @pytest.mark.parametrize("dangerous_url", DANGEROUS_URLS)
    def test_dangerous_schemes_neutralized_to_hash(self, dangerous_url: str):
        """Dangerous URI schemes (javascript:, data:, vbscript:) are sanitized to '#'."""
        sanitized = sanitize_url(dangerous_url)
        assert sanitized == "#", f"Failed to neutralize dangerous scheme: {dangerous_url}"

    @pytest.mark.parametrize("safe_url", SAFE_URLS)
    def test_safe_urls_preserved(self, safe_url: str):
        """Standard HTTP/HTTPS and relative URLs are preserved."""
        assert sanitize_url(safe_url) == safe_url

    def test_none_and_empty_url_fallback(self):
        """None and empty string URLs default to '#', while whitespace-only returns empty string."""
        assert sanitize_url(None) == "#"
        assert sanitize_url("") == "#"
        # Empirical observation: '   ' is stripped to '' which does not trigger startswith check
        assert sanitize_url("   ") == ""

    def test_badge_with_malicious_click_url_rendered_safely(self):
        """Malicious click_url passed to badge renderer must be sanitized in output SVG."""
        svg = build_badge_svg(
            repo_name="flask",
            stars=68000,
            ad={"sponsor_name": "Bad Actor", "headline": "Free V-Bucks"},
            click_url="javascript:alert('pwned')",
        )
        root = validate_svg_xml(svg)
        anchors = [e for e in root.iter() if e.tag.endswith("a")]
        assert len(anchors) >= 1
        for a in anchors:
            href = a.attrib.get("href") or a.attrib.get(f"{XLINK_NS}href")
            assert href == "#"
            assert "javascript" not in href.lower()


# =====================================================================
# 3. Unicode and Internationalization Support
# =====================================================================

class TestUnicodeAndInternationalization:
    """Stress-test UTF-8 rendering, non-ASCII repository names, and foreign typography."""

    UNICODE_SAMPLES = [
        "こんにちは世界",             # Japanese (Hiragana & Kanji)
        "开源项目·动态徽章",           # Simplified Chinese
        "안녕하세요-세계",             # Korean Hangul
        "مشروع-مفتوح-المصدر",          # Arabic (RTL)
        "פרויקט-קוד-פתוח",             # Hebrew (RTL)
        "репозиторий_открытый_код",    # Russian Cyrillic
        "déjà-vu_café_naïve",          # Latin Extended Accented
        "🚀_awesome_repo_🔥_⭐",      # Emojis
        "∀x∈ℝ: x²≥0",                  # Mathematical Notation
        "Zażółć gęślą jaźń",           # Polish diacritics
    ]

    @pytest.mark.parametrize("name", UNICODE_SAMPLES)
    def test_unicode_repo_name_renders_valid_xml(self, name: str):
        """Unicode repository names render cleanly and preserve valid XML syntax."""
        svg = build_badge_svg(
            repo_name=name,
            stars=1200,
            language="Python",
            ci_status="passing",
        )
        root = validate_svg_xml(svg)
        assert root is not None
        assert "svg" in root.tag.lower()

    def test_unicode_end_to_end_badge_request(self, client: TestClient, db_session: Session):
        """Full HTTP endpoint request for URL-encoded Unicode repository name."""
        encoded_name = urllib.parse.quote("こんにちは世界")
        repo = create_test_repository(db_session, owner="i18n-org", name="こんにちは世界")
        create_test_ad(db_session)

        resp = client.get(f"/badge/{repo.owner}/{encoded_name}.svg")
        assert resp.status_code == 200
        assert "image/svg+xml" in resp.headers.get("content-type", "")
        root = assert_valid_svg_xml(resp.text)
        assert root is not None


# =====================================================================
# 4. Star Count Formatting Boundaries
# =====================================================================

class TestStarFormattingBoundaries:
    """Boundary and range tests for format_stars."""

    TEST_VECTORS = [
        (0, "0"),
        (1, "1"),
        (999, "999"),
        (1000, "1k"),
        (1200, "1.2k"),
        (1250, "1.2k"),
        (9900, "9.9k"),
        (10000, "10k"),
        (51000, "51k"),
        (68000, "68k"),
        (102800, "102.8k"),
        (999900, "999.9k"),
        (1000000, "1M"),
        (1500000, "1.5M"),
        (2000000, "2M"),
        (12500000, "12.5M"),
        (100000000, "100M"),
    ]

    @pytest.mark.parametrize("stars, expected", TEST_VECTORS)
    def test_star_formatting_ranges(self, stars: int, expected: str):
        """Verify format_stars conforms to exact range thresholds."""
        result = format_stars(stars)
        assert result == expected

    @pytest.mark.parametrize("invalid_input", [-1, -500, None, "1000", [], {}])
    def test_invalid_star_inputs_gracefully_default_to_zero(self, invalid_input):
        """Negative numbers, None, and non-numeric inputs cleanly return '0'."""
        assert format_stars(invalid_input) == "0"

    def test_float_stars_handled_without_crash(self):
        """Float numbers are cast to integers cleanly."""
        assert format_stars(1500000.0) == "1.5M"
        assert format_stars(1200.7) == "1.2k"


# =====================================================================
# 5. CI/CD Status Dimensions & Missing CI
# =====================================================================

class TestCiStatusAndPillRendering:
    """Verify CI/CD status pill colors, text, and absence."""

    CI_CASES = [
        ("passing", "#238636", "#ffffff"),
        ("passed", "#238636", "#ffffff"),
        ("success", "#238636", "#ffffff"),
        ("failing", "#da3633", "#ffffff"),
        ("failed", "#da3633", "#ffffff"),
        ("error", "#da3633", "#ffffff"),
        ("pending", "#9e6a03", "#ffffff"),
        ("running", "#9e6a03", "#ffffff"),
        ("build", "#9e6a03", "#ffffff"),
        ("unknown_state", "#30363d", "#c9d1d9"),
        (None, "#30363d", "#8b949e"),
    ]

    @pytest.mark.parametrize("ci_input, exp_bg, exp_fg", CI_CASES)
    def test_get_ci_colors(self, ci_input: str | None, exp_bg: str, exp_fg: str):
        """Verify CI/CD status translates to canonical GitHub dark theme colors."""
        bg, fg = get_ci_colors(ci_input)
        assert bg == exp_bg
        assert fg == exp_fg

    def test_ci_status_none_omits_ci_pill_element(self):
        """When ci_status is None, the <g id='ci-pill'> element must not be rendered."""
        svg = build_badge_svg(
            repo_name="my-repo",
            stars=100,
            language="Python",
            ci_status=None,
        )
        assert "ci-pill" not in svg
        root = validate_svg_xml(svg)
        assert root is not None

    def test_both_language_and_ci_status_none(self):
        """When both language and ci_status are None, layout remains valid and clean."""
        svg = build_badge_svg(
            repo_name="minimal-repo",
            stars=0,
            language=None,
            ci_status=None,
        )
        assert "ci-pill" not in svg
        assert "lang-pill" not in svg
        root = validate_svg_xml(svg)
        assert root is not None

    def test_ci_status_present_when_language_is_none(self):
        """When language is None but CI status exists, CI pill is rendered at translate(0, 0)."""
        svg = build_badge_svg(
            repo_name="ci-only-repo",
            stars=42,
            language=None,
            ci_status="passing",
        )
        assert "ci-pill" in svg
        assert "translate(0, 0)" in svg
        root = validate_svg_xml(svg)
        assert root is not None


# =====================================================================
# 6. SVG Spec & Responsive Dimensions
# =====================================================================

class TestSvgSpecAndResponsiveDimensions:
    """Verify SVG adheres to W3C SVG spec and PROJECT.md responsive dimension contract."""

    def test_badge_svg_dimensions_and_viewbox(self):
        """Badge SVG root element has width='500', height='110', and viewBox='0 0 500 110'."""
        svg = build_badge_svg("fastapi", 70000, "Python", "passing")
        root = validate_svg_xml(svg)
        assert root.tag.endswith("svg")
        assert root.attrib.get("width") == "500"
        assert root.attrib.get("height") == "110"
        assert root.attrib.get("viewBox") == "0 0 500 110"

    def test_error_svg_dimensions_and_viewbox(self):
        """Error SVG root element has responsive viewBox='0 0 500 110'."""
        err_svg = build_error_svg("Not Found", "Repository does not exist", 404)
        root = validate_svg_xml(err_svg)
        assert root.tag.endswith("svg")
        assert root.attrib.get("width") == "500"
        assert root.attrib.get("height") == "110"
        assert root.attrib.get("viewBox") == "0 0 500 110"


# =====================================================================
# 7. Hyperlink Extraction Oracle
# =====================================================================

class TestHyperlinkExtractionOracle:
    """Verify standard SVG anchor elements (<a ...>) wrap ad regions for click-throughs."""

    def test_ad_hyperlink_extraction_with_active_sponsor(self):
        """SVG anchor element wraps ad card and contains exact click redirect URL."""
        click_target = "/click/42/10"
        ad_model = {
            "sponsor_name": "Datadog",
            "headline": "Cloud Monitoring at Scale",
            "call_to_action": "Try Free",
            "click_url": "https://datadog.com",
        }
        svg = build_badge_svg(
            repo_name="flask",
            stars=68000,
            ad=ad_model,
            click_url=click_target,
        )
        root = validate_svg_xml(svg)

        # Locate <a> tag within SVG namespace
        anchors = [e for e in root.iter() if e.tag.endswith("a")]
        assert len(anchors) == 1, f"Expected exactly 1 anchor tag, found {len(anchors)}"
        anchor = anchors[0]

        # Check standard href and xlink:href
        href = anchor.attrib.get("href")
        xlink_href = anchor.attrib.get(f"{XLINK_NS}href") or anchor.attrib.get("xlink:href")
        assert href == click_target
        assert xlink_href == click_target
        assert anchor.attrib.get("target") == "_blank"
        assert "noopener" in anchor.attrib.get("rel", "")

        # Check that anchor contains the ad card rect and CTA button
        child_tags = [c.tag.split("}")[-1] for c in anchor]
        assert "rect" in child_tags
        assert "g" in child_tags

    def test_fallback_hyperlink_extraction_when_no_ad(self):
        """Fallback SVG anchor wraps community card linking to claim or onboard endpoint."""
        claim_url = "/maintainers/claim?repo=pallets/flask"
        svg = build_badge_svg(
            repo_name="flask",
            stars=68000,
            ad=None,
            click_url=claim_url,
        )
        root = validate_svg_xml(svg)
        anchors = [e for e in root.iter() if e.tag.endswith("a")]
        assert len(anchors) == 1
        anchor = anchors[0]
        assert anchor.attrib.get("href") == claim_url
        assert anchor.attrib.get("target") == "_blank"


# =====================================================================
# 8. RFC 7232 ETag Validation & HTTP 304 Negotiation
# =====================================================================

class TestEtagCachingAndConditionalNegotiation:
    """Stress-test RFC 7232 ETag computation, weak ETag parsing, and 304 Not Modified."""

    def test_etag_is_sha256_hex_quoted_string(self):
        """compute_svg_etag returns standard RFC quoted 64-hex SHA-256 string."""
        sample_svg = "<svg>test</svg>"
        etag = compute_svg_etag(sample_svg)
        assert etag.startswith('"') and etag.endswith('"')
        hex_content = etag.strip('"')
        assert len(hex_content) == 64
        assert re.match(r"^[0-9a-f]{64}$", hex_content)

    def test_etag_is_deterministic_and_sensitive_to_change(self):
        """Identical content yields identical ETag; single character change yields new ETag."""
        svg1 = "<svg width='500'>A</svg>"
        svg2 = "<svg width='500'>A</svg>"
        svg3 = "<svg width='500'>B</svg>"
        assert compute_svg_etag(svg1) == compute_svg_etag(svg2)
        assert compute_svg_etag(svg1) != compute_svg_etag(svg3)

    def test_http_304_strong_etag(self, client: TestClient, db_session: Session):
        """Matching strong ETag returns 304 Not Modified with empty payload."""
        repo = create_test_repository(db_session, owner="etag-test", name="repo-strong")
        create_test_ad(db_session)

        res1 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
        assert res1.status_code == 200
        etag = res1.headers.get("etag")
        assert etag is not None

        res2 = client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers={"if-none-match": etag})
        assert res2.status_code == 304
        assert res2.text == ""

    def test_http_304_weak_etag(self, client: TestClient, db_session: Session):
        """Weak ETag prefix W/ is safely stripped and parsed."""
        repo = create_test_repository(db_session, owner="etag-test", name="repo-weak")
        create_test_ad(db_session)

        res1 = client.get(f"/badge/{repo.owner}/{repo.name}.svg")
        etag = res1.headers.get("etag")
        weak_etag = f"W/{etag}"

        res2 = client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers={"if-none-match": weak_etag})
        assert res2.status_code == 304
        assert res2.text == ""

    def test_http_304_wildcard_if_none_match(self, client: TestClient, db_session: Session):
        """If-None-Match: * returns 304 Not Modified."""
        repo = create_test_repository(db_session, owner="etag-test", name="repo-wildcard")
        create_test_ad(db_session)

        res = client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers={"if-none-match": "*"})
        assert res.status_code == 304

    def test_http_200_when_etag_differs(self, client: TestClient, db_session: Session):
        """Mismatched ETag returns full HTTP 200 with new SVG payload."""
        repo = create_test_repository(db_session, owner="etag-test", name="repo-diff")
        create_test_ad(db_session)

        res = client.get(
            f"/badge/{repo.owner}/{repo.name}.svg",
            headers={"if-none-match": '"0000000000000000000000000000000000000000000000000000000000000000"'},
        )
        assert res.status_code == 200
        assert "image/svg+xml" in res.headers.get("content-type", "")


# =====================================================================
# 9. Honest Error Handling Integrity (Anti-Pattern Compliance)
# =====================================================================

class TestHonestErrorHandlingIntegrity:
    """Verify system strictly adheres to the 'Stop Doing This' Anti-Pattern rules."""

    def test_nonexistent_repo_returns_404_error_svg(self, client: TestClient):
        """Nonexistent repo returns authentic 404 status code with honest error SVG."""
        resp = client.get("/badge/ghost-developer-404/ghost-repo-404.svg")
        assert resp.status_code == 404
        assert "image/svg+xml" in resp.headers.get("content-type", "")
        root = assert_valid_svg_xml(resp.text)
        assert root is not None
        assert "404" in resp.text

    def test_zero_synthetic_personas_in_error_payload(self, client: TestClient):
        """Error payloads strictly contain NO 'Thomas Vance', 'Theo Vance', or fake mortgages."""
        resp = client.get("/badge/unindexed-org/unindexed-repo.svg")
        content_lower = resp.text.lower()
        assert "thomas vance" not in content_lower
        assert "theo vance" not in content_lower
        assert "warranty deed" not in content_lower
        assert "jpmorgan" not in content_lower
        assert "chase deed" not in content_lower


# =====================================================================
# 10. Multithreaded SVG Rendering Concurrency Stress
# =====================================================================

class TestConcurrencyAndThreadSafety:
    """Stress-test concurrent badge generation across multiple threads."""

    def test_concurrent_badge_rendering_threads(self):
        """Concurrently compile 50 badges with varied parameters to detect race conditions."""
        def worker(idx: int) -> str:
            return build_badge_svg(
                repo_name=f"concurrent-repo-{idx}",
                stars=idx * 100,
                language="Python" if idx % 2 == 0 else "Rust",
                ci_status="passing" if idx % 3 == 0 else "failing",
                ad={"sponsor_name": f"Sponsor {idx}", "headline": f"Fast cloud {idx}"},
                click_url=f"/click/{idx}/{idx}",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker, i) for i in range(50)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert len(results) == 50
        for svg in results:
            root = validate_svg_xml(svg)
            assert root is not None
