"""
Badge Compilation Service (app/services/badge_service.py).

Compiles dynamic SVG badges via Jinja2 with strict XML autoescaping,
performs XML syntax validation via xml.etree.ElementTree, formats numbers,
computes cryptographic SHA-256 ETags, and renders honest error badges.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import jinja2

# Locate app/templates directory
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def should_autoescape(template_name: str | None) -> bool:
    """Strictly autoescape XML/SVG entities for all templates (.svg, .xml, .j2, .svg.j2)."""
    if not template_name:
        return True
    lower = template_name.lower()
    return (
        lower.endswith(".svg")
        or lower.endswith(".xml")
        or lower.endswith(".html")
        or lower.endswith(".j2")
        or ".svg." in lower
        or ".xml." in lower
    )


# Configure Jinja2 environment with strict XML/SVG/HTML autoescape to prevent XSS and XML injection
jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(TEMPLATES_DIR),
    autoescape=should_autoescape,
    trim_blocks=True,
    lstrip_blocks=True,
)

# Canonical GitHub programming language color mappings
LANGUAGE_COLORS: dict[str, str] = {
    "python": "#3572A5",
    "typescript": "#3178c6",
    "javascript": "#f1e05a",
    "rust": "#dea584",
    "go": "#00ADD8",
    "c++": "#f34b7d",
    "c": "#555555",
    "c#": "#178600",
    "ruby": "#701516",
    "java": "#b07219",
    "php": "#4F5D95",
    "swift": "#F05138",
    "kotlin": "#A97BFF",
    "shell": "#89e051",
    "html": "#e34c26",
    "css": "#563d7c",
    "haskell": "#5e5086",
    "scala": "#c22d40",
    "elixir": "#6e4a7e",
    "clojure": "#db5855",
    "dart": "#00B4AB",
    "lua": "#000080",
    "r": "#198CE7",
    "julia": "#a270ba",
}

DEFAULT_LANGUAGE_COLOR = "#8b949e"


def get_language_color(language: str | None) -> str:
    """Resolve GitHub official hex color for programming language (case-insensitive)."""
    if not language:
        return DEFAULT_LANGUAGE_COLOR
    normalized = language.strip().lower()
    return LANGUAGE_COLORS.get(normalized, DEFAULT_LANGUAGE_COLOR)


def format_stars(stars: int) -> str:
    """
    Format GitHub star count into compact human-readable string.
    Examples:
      0 -> '0'
      950 -> '950'
      1200 -> '1.2k'
      51000 -> '51k'
      68000 -> '68k'
      102800 -> '102.8k'
      1500000 -> '1.5M'
    """
    if not isinstance(stars, (int, float)) or stars < 0:
        return "0"
    stars_int = int(stars)
    if stars_int >= 1_000_000:
        val = stars_int / 1_000_000
        return f"{int(val)}M" if val.is_integer() else f"{val:.1f}M"
    elif stars_int >= 1_000:
        val = stars_int / 1_000
        return f"{int(val)}k" if val.is_integer() else f"{val:.1f}k"
    else:
        return str(stars_int)


def truncate_text(text: str | None, length: int = 30, suffix: str = "...") -> str:
    """Safely truncate text to a maximum length while preserving clean rendering."""
    if not text:
        return ""
    text_str = str(text).strip()
    if len(text_str) <= length:
        return text_str
    cutoff = max(0, length - len(suffix))
    return text_str[:cutoff].rstrip() + suffix


def sanitize_url(url: str | None) -> str:
    """
    Sanitize hyperlink URL against dangerous schemes like javascript: or data:.
    Allows http://, https://, and relative paths.
    """
    if not url:
        return "#"
    clean = str(url).strip()
    lower = clean.lower()
    if lower.startswith("javascript:") or lower.startswith("data:") or lower.startswith("vbscript:"):
        return "#"
    return clean


def get_ci_colors(ci_status: str | None) -> tuple[str, str]:
    """Return (bg_color, text_color) tuple for CI/CD badge pill."""
    if not ci_status:
        return ("#30363d", "#8b949e")
    lower = ci_status.lower()
    if any(w in lower for w in ["fail", "error"]):
        return ("#da3633", "#ffffff")
    elif any(w in lower for w in ["run", "pending", "build"]):
        return ("#9e6a03", "#ffffff")
    elif any(w in lower for w in ["pass", "success"]):
        return ("#238636", "#ffffff")
    else:
        return ("#30363d", "#c9d1d9")


# Register custom filters into Jinja environment
jinja_env.filters["format_stars"] = format_stars
jinja_env.filters["truncate_text"] = truncate_text
jinja_env.filters["sanitize_url"] = sanitize_url
jinja_env.filters["lang_color"] = get_language_color


def validate_svg_xml(svg_text: str) -> ET.Element:
    """
    Strictly validate that the rendered SVG string is syntactically well-formed XML
    with a root <svg> element.

    Raises:
        ValueError: If XML parsing fails or root tag is not svg.
    """
    if not svg_text or not svg_text.strip():
        raise ValueError("Rendered SVG content cannot be empty.")
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        raise ValueError(f"Rendered SVG failed XML syntax validation: {exc}") from exc
    tag = root.tag.lower()
    if not tag.endswith("svg"):
        raise ValueError(f"Root XML element must be <svg>, received <{root.tag}>")
    return root


def compute_svg_etag(svg_content: str) -> str:
    """
    Compute a cryptographic SHA-256 HTTP entity tag (ETag) for caching and 304 validation.
    Returns standard RFC 7232 quoted hexadecimal string: '"<sha256-hex>"'.
    """
    digest = hashlib.sha256(svg_content.encode("utf-8")).hexdigest()
    return f'"{digest}"'


def build_badge_svg(
    repo_name: str,
    stars: int,
    language: str | None = None,
    ci_status: str | None = None,
    ad: Any | None = None,
    click_url: str | None = None,
    owner: str | None = None,
    style: str = "banner",
) -> str:
    """
    Compile dynamic SVG badge string matching PROJECT.md interface contract.

    Args:
        repo_name: Repository name (e.g. 'flask' or 'requests')
        stars: Live star count (e.g. 68000)
        language: Primary programming language (e.g. 'Python')
        ci_status: CI/CD status ('passing', 'failing', or None)
        ad: Matched Ad model instance, dict, or None
        click_url: Direct click-redirect URL (e.g. '/click/1/1')
        owner: Optional repository owner (e.g. 'pallets')
        style: Badge layout style ('banner' or 'shield', defaults to 'banner')

    Returns:
        Valid XML SVG string.
    """
    template_name = "shield.svg.j2" if style.lower() == "shield" else "badge.svg.j2"
    template = jinja_env.get_template(template_name)

    has_ad = ad is not None
    sponsor_name = ""
    headline = ""
    cta_text = "Learn More"

    if has_ad:
        if isinstance(ad, dict):
            sponsor_name = ad.get("sponsor_name", "") or ""
            headline = ad.get("headline", "") or ""
            cta_text = ad.get("call_to_action") or ad.get("cta_text") or "Learn More"
            raw_click_url = click_url or ad.get("click_url", "#")
        else:
            sponsor_name = getattr(ad, "sponsor_name", "") or ""
            headline = getattr(ad, "headline", "") or ""
            cta_text = (
                getattr(ad, "call_to_action", None)
                or getattr(ad, "cta_text", None)
                or "Learn More"
            )
            raw_click_url = click_url or getattr(ad, "click_url", "#")
    else:
        raw_click_url = click_url or "/onboard"

    effective_click_url = sanitize_url(raw_click_url)
    ci_bg_color, ci_text_color = get_ci_colors(ci_status)

    rendered = template.render(
        repo_name=repo_name,
        formatted_stars=format_stars(stars),
        language=language.strip() if language and language.strip() else None,
        lang_color=get_language_color(language),
        ci_status=ci_status.strip() if ci_status and ci_status.strip() else None,
        ci_bg_color=ci_bg_color,
        ci_text_color=ci_text_color,
        has_ad=has_ad,
        sponsor_name=sponsor_name,
        headline=headline,
        cta_text=cta_text,
        click_url=effective_click_url,
    )

    # Strictly validate XML syntax before returning
    validate_svg_xml(rendered)
    return rendered


def build_error_svg(
    error_title: str | None = None,
    error_message: str | None = None,
    error_code: int | None = None,
    title: str | None = None,
    message: str | None = None,
    status_code: int | None = None,
) -> str:
    """
    Compile honest transparent error SVG badge for non-existent repositories,
    rate limits, or upstream failures. Strictly zero synthetic personas or mock figures.

    Args:
        error_title / title: Human-readable error heading
        error_message / message: Transparent diagnostic explanation
        error_code / status_code: HTTP status code (404, 429, 500, etc.)

    Returns:
        Valid XML SVG string.
    """
    eff_title = error_title or title or "Repository Not Found"
    eff_message = error_message or message or "The requested repository could not be located or verified."
    eff_code = error_code or status_code or 404

    template = jinja_env.get_template("error.svg.j2")
    rendered = template.render(
        error_title=eff_title,
        error_message=eff_message,
        error_code=eff_code,
    )
    validate_svg_xml(rendered)
    return rendered


def compile_and_validate_badge(
    repo_name: str,
    stars: int,
    language: str | None = None,
    ci_status: str | None = None,
    ad: Any | None = None,
    click_url: str | None = None,
    owner: str | None = None,
    style: str = "banner",
) -> tuple[str, str]:
    """
    Convenience method compiling badge SVG and computing its SHA-256 ETag.

    Returns:
        Tuple of (svg_content, etag)
    """
    svg_content = build_badge_svg(
        repo_name=repo_name,
        stars=stars,
        language=language,
        ci_status=ci_status,
        ad=ad,
        click_url=click_url,
        owner=owner,
        style=style,
    )
    etag = compute_svg_etag(svg_content)
    return svg_content, etag
