"""Service layer components for SVG badges, GitHub metadata, and ad matching."""

from app.services.badge_service import (
    build_badge_svg,
    build_error_svg,
    compile_and_validate_badge,
    compute_svg_etag,
    format_stars,
    get_language_color,
    truncate_text,
    validate_svg_xml,
)
from app.services.github_service import (
    GitHubRepoData,
    GitHubService,
    MemoryTTLCache,
    clear_github_cache,
    close_github_client,
    fetch_ci_status,
    fetch_repository_metadata,
    get_or_fetch_repository,
    get_or_fetch_repository_data,
    github_service,
)
from app.services.matching_service import (
    get_platform_onboarding_ad,
    match_ad_cascade,
    match_ad_for_repository,
)

__all__ = [
    "GitHubRepoData",
    "GitHubService",
    "MemoryTTLCache",
    "build_badge_svg",
    "build_error_svg",
    "clear_github_cache",
    "close_github_client",
    "compile_and_validate_badge",
    "compute_svg_etag",
    "fetch_ci_status",
    "fetch_repository_metadata",
    "format_stars",
    "get_language_color",
    "get_or_fetch_repository",
    "get_or_fetch_repository_data",
    "get_platform_onboarding_ad",
    "github_service",
    "match_ad_cascade",
    "match_ad_for_repository",
    "truncate_text",
    "validate_svg_xml",
]
