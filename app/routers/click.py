"""
Click Tracking API Router (app/routers/click.py).

Provides:
- GET /click/active/{repo_id}: Dynamically matches active sponsor for repo, logs click, and redirects.
- GET /click/{ad_id}/{repo_id}: Logs click event, deducts CPC budget, and issues HTTP 302 Found redirect.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Request, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.tracking_service import (
    extract_client_ip,
    record_active_click,
    record_click,
)

router = APIRouter(tags=["Click Tracking"])

CACHE_CONTROL_REDIRECT = "no-cache, no-store, must-revalidate"
REDIRECT_HEADERS = {
    "Cache-Control": CACHE_CONTROL_REDIRECT,
    "Pragma": "no-cache",
    "Expires": "0",
}


@router.get("/click/active/{repo_id}", response_class=Response)
def handle_active_click_redirect(
    repo_id: int = Path(..., description="Repository inventory ID"),
    request: Request = None,
    db: Session = Depends(get_db),
):
    """
    Dynamically resolve the currently active matched sponsor for repo_id,
    log click event, deduct budget, and issue HTTP 302 redirect.

    Returns:
    - 302 Found: Redirect to dynamically matched sponsor destination URL.
    - 404 Not Found: When repo does not exist or no active sponsor campaign matches.
    """
    client_ip = extract_client_ip(request)
    user_agent = request.headers.get("user-agent", "") if request else ""
    referer = (request.headers.get("referer") or request.headers.get("referrer")) if request else None
    incoming_params = dict(request.query_params) if request and request.query_params else None

    destination_url, _ = record_active_click(
        db=db,
        repo_id=repo_id,
        client_ip=client_ip,
        user_agent=user_agent,
        referer=referer,
        incoming_params=incoming_params,
    )

    return Response(
        status_code=status.HTTP_302_FOUND,
        headers={
            "Location": destination_url,
            **REDIRECT_HEADERS,
        },
    )


@router.get("/click/{ad_id}/{repo_id}", response_class=Response)
def handle_click_redirect(
    ad_id: int = Path(..., description="Target sponsor ad campaign ID"),
    repo_id: int = Path(..., description="Repository inventory ID"),
    request: Request = None,
    db: Session = Depends(get_db),
):
    """
    Log sponsor click event, deduct CPC budget, and issue HTTP 302 redirect.

    Returns:
    - 302 Found: Redirect to sponsor destination URL with no-cache headers.
    - 404 Not Found: When ad or repo does not exist, or campaign budget is exhausted.
    - 422 Unprocessable Entity: When identifiers are non-numeric.
    """
    client_ip = extract_client_ip(request)
    user_agent = request.headers.get("user-agent", "") if request else ""
    referer = (request.headers.get("referer") or request.headers.get("referrer")) if request else None
    incoming_params = dict(request.query_params) if request and request.query_params else None

    destination_url, _ = record_click(
        db=db,
        ad_id=ad_id,
        repo_id=repo_id,
        client_ip=client_ip,
        user_agent=user_agent,
        referer=referer,
        incoming_params=incoming_params,
    )

    return Response(
        status_code=status.HTTP_302_FOUND,
        headers={
            "Location": destination_url,
            **REDIRECT_HEADERS,
        },
    )
