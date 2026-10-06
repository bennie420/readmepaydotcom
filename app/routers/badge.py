"""
Badge API Router (app/routers/badge.py).

Provides:
- GET /badge/{owner}/{repo}.svg: High-concurrency dynamic SVG badge compiler
  with live metadata, targeted ad matching, embedded hyperlinks,
  RFC 7232 ETag caching, HTTP 304 conditional negotiation,
  and transparent honest 404 error SVG handling.
"""

from __future__ import annotations

import logging
import urllib.parse

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    Request,
    Response,
    status,
)
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.badge_service import (
    build_badge_svg,
    build_error_svg,
    compute_svg_etag,
)
from app.services.github_service import get_or_fetch_repository
from app.services.matching_service import match_ad_for_repository
from app.services.tracking_service import (
    extract_client_info,
    record_impression,
)

logger = logging.getLogger("router_badge")

router = APIRouter(tags=["Badges"])

CACHE_MAX_AGE = 3600
CACHE_CONTROL_HEADER = f"public, max-age={CACHE_MAX_AGE}"
SVG_MEDIA_TYPE = "image/svg+xml; charset=utf-8"


def extract_client_ip(request: Request) -> str:
    """Extract client IP handling forward headers."""
    x_forwarded_for = request.headers.get("x-forwarded-for")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "127.0.0.1"


def matches_if_none_match(client_etag: str | None, current_etag: str) -> bool:
    """Evaluate RFC 7232 If-None-Match condition."""
    if not client_etag:
        return False
    tag = client_etag.strip()
    if tag == "*":
        return True
    unquoted_client = tag.removeprefix("W/").strip('"')
    unquoted_current = current_etag.removeprefix("W/").strip('"')
    return unquoted_client == unquoted_current


@router.get("/badge/{owner}/{repo}.svg", response_class=Response)
async def get_badge_svg(
    owner: str,
    repo: str,
    request: Request,
    background_tasks: BackgroundTasks,
    style: str = "banner",
    if_none_match: str | None = Header(None, alias="if-none-match"),
    db: Session = Depends(get_db),
):
    """
    Render responsive dynamic SVG README badge for given repository.

    Returns:
    - 200 OK: Valid XML SVG with live stars, language, CI status, and sponsor ad.
    - 304 Not Modified: When If-None-Match header matches current ETag.
    - 404 Not Found: Transparent error SVG when repository does not exist on GitHub.
    """
    # 1. URL decode and validate segments
    decoded_owner = urllib.parse.unquote(owner).strip()
    decoded_repo = urllib.parse.unquote(repo).strip()

    if not decoded_owner or not decoded_repo:
        error_svg = build_error_svg(
            error_title="Invalid Repository Identifier",
            error_message="Both repository owner and repository name are required.",
            error_code=404,
        )
        return Response(
            content=error_svg,
            status_code=status.HTTP_404_NOT_FOUND,
            media_type=SVG_MEDIA_TYPE,
            headers={
                "Content-Type": SVG_MEDIA_TYPE,
                "Cache-Control": "public, max-age=300",
            },
        )

    # 2. Database and GitHub API hydration
    repository = await get_or_fetch_repository(db, decoded_owner, decoded_repo)
    if repository is None:
        # Transparent honest 404 error with valid XML error SVG
        error_svg = build_error_svg(
            error_title="Repository Not Found",
            error_message=f"Repository '{decoded_owner}/{decoded_repo}' could not be located on GitHub.",
            error_code=404,
        )
        return Response(
            content=error_svg,
            status_code=status.HTTP_404_NOT_FOUND,
            media_type=SVG_MEDIA_TYPE,
            headers={
                "Content-Type": SVG_MEDIA_TYPE,
                "Cache-Control": "public, max-age=300",
                "X-Error-Reason": "Repository Not Found",
            },
        )

    # 3. Sponsor campaign matching (Tier 1 language -> Tier 2 general)
    matched_ad = match_ad_for_repository(
        db,
        repository.primary_language,
        allow_platform_invite=False,
    )

    # 4. Construct click redirect hyperlink
    if matched_ad is not None and getattr(matched_ad, "id", None) and matched_ad.id > 0:
        click_url = f"/click/{matched_ad.id}/{repository.id}"
    else:
        click_url = f"/maintainers/claim?repo={repository.owner}/{repository.name}"

    # 5. Non-blocking impression tracking via BackgroundTasks
    if matched_ad is not None and getattr(matched_ad, "id", None) and matched_ad.id > 0:
        client_info = extract_client_info(request)
        background_tasks.add_task(
            record_impression,
            db=db,
            ad_id=matched_ad.id,
            repo_id=repository.id,
            client_ip=client_info.ip,
            user_agent=client_info.user_agent,
            is_camo=client_info.is_camo,
        )

    # 6. Render dynamic badge SVG
    svg_content = build_badge_svg(
        repo_name=repository.name,
        stars=repository.stars,
        language=repository.primary_language,
        ci_status=repository.ci_status,
        ad=matched_ad,
        click_url=click_url,
        owner=repository.owner,
        style=style,
    )

    # 7. ETag generation and HTTP 304 negotiation
    etag = compute_svg_etag(svg_content)
    if matches_if_none_match(if_none_match, etag):
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers={
                "ETag": etag,
                "Cache-Control": CACHE_CONTROL_HEADER,
            },
        )

    # 8. Return HTTP 200 OK
    return Response(
        content=svg_content,
        status_code=status.HTTP_200_OK,
        media_type=SVG_MEDIA_TYPE,
        headers={
            "Content-Type": SVG_MEDIA_TYPE,
            "Cache-Control": CACHE_CONTROL_HEADER,
            "ETag": etag,
        },
    )
