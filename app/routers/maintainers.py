"""
Maintainer Onboarding and Snippet Generation API Router (app/routers/maintainers.py).

Provides:
- POST /maintainers/claim (and aliases /api/repos/claim, /api/maintainers/claim):
  Claim repository ownership and register maintainer payout address.
- GET /maintainers/claim: Informational onboarding guidance.
- GET /maintainers/snippet/{owner}/{repo} (and alias /api/repos/{owner}/{repo}/snippet):
  Generate copy-pasteable badge snippets (Markdown, HTML, RST) linking to dynamic click redirect.
"""

from __future__ import annotations

import logging
import urllib.parse
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.repository import Repository
from app.schemas.repo import (
    ClaimRepoRequest,
    ClaimRepoResponse,
    RepoResponse,
    SnippetResponse,
)
from app.services.github_service import get_or_fetch_repository

logger = logging.getLogger("router_maintainers")

router = APIRouter(tags=["Maintainers"])


def resolve_base_url(request: Request | None = None) -> str:
    """
    Resolve the canonical base URL for badge and click endpoints.
    Handles reverse-proxy headers and FastAPI TestClient hosts.
    """
    if request is not None:
        forwarded_proto = request.headers.get("x-forwarded-proto")
        forwarded_host = request.headers.get("x-forwarded-host")
        if forwarded_proto and forwarded_host:
            proto = forwarded_proto.split(",")[0].strip()
            host = forwarded_host.split(",")[0].strip()
            return f"{proto}://{host}"

        req_base = str(request.base_url).rstrip("/")
        if req_base:
            return req_base

    return getattr(settings, "BASE_URL", "http://localhost:8080").rstrip("/")


def generate_badge_snippets(owner: str, name: str, repo_id: int, base_url: str = "") -> dict[str, str]:
    """Generate Markdown, HTML, and RST badge snippets pointing to dynamic click tracking."""
    base = base_url.rstrip("/") if base_url else ""
    badge_url = f"{base}/badge/{owner}/{name}.svg"
    click_url = f"{base}/click/active/{repo_id}"
    return {
        "markdown": f"[![Sponsorship Badge]({badge_url})]({click_url})",
        "html": f'<a href="{click_url}"><img src="{badge_url}" alt="Sponsorship Badge" /></a>',
        "rst": f".. image:: {badge_url}\n   :target: {click_url}\n   :alt: Sponsorship Badge",
        "badge_url": badge_url,
        "click_url": click_url,
    }


# Contract alias matching PROJECT.md
def generate_markdown_snippet(base_url: str, owner: str, name: str, repo_id: int) -> dict[str, str]:
    return generate_badge_snippets(owner=owner, name=name, repo_id=repo_id, base_url=base_url)


async def claim_repository(
    db: Session,
    owner: str,
    name: str,
    maintainer_handle: str,
    payout_address: str | None = None,
) -> Repository:
    """
    Interface contract function:
    1. Case-insensitive lookup in local SQLite DB.
    2. If missing, query GitHub REST API v3 via get_or_fetch_repository.
    3. If not found anywhere, raise HTTP 404 (strictly honest zero-mock).
    4. If already claimed, raise HTTP 409 Conflict.
    5. Atomically set claimed=True, maintainer_handle, claimed_at, and optional payout_address.
    6. Commit and return updated Repository.
    """
    clean_owner = owner.strip()
    clean_name = name.strip()
    clean_handle = maintainer_handle.strip()
    clean_payout = payout_address.strip() if payout_address else None

    # 1. Local Database Lookup (Case-insensitive)
    repo = (
        db.query(Repository)
        .filter(
            func.lower(Repository.owner) == clean_owner.lower(),
            func.lower(Repository.name) == clean_name.lower(),
        )
        .first()
    )

    # 2. Fetch from GitHub REST API v3 if unindexed
    if repo is None:
        repo = await get_or_fetch_repository(db, clean_owner, clean_name)

    # 3. Honest 404 if repo does not exist
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{clean_owner}/{clean_name}' not found on GitHub or platform registry.",
        )

    # 4. Check if already claimed -> 409 Conflict
    if repo.claimed:
        claimed_by = repo.maintainer_handle or repo.claimed_by or "another maintainer"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Repository '{repo.owner}/{repo.name}' has already been claimed by '{claimed_by}'.",
        )

    # 5. Atomic Update Execution
    now = datetime.now(UTC)
    updated_rows = (
        db.query(Repository)
        .filter(
            Repository.id == repo.id,
            Repository.claimed == False,
        )
        .update(
            {
                Repository.claimed: True,
                Repository.claimed_by: clean_handle,
                Repository.claimed_at: now,
                Repository.payout_address: clean_payout,
                Repository.updated_at: now,
            },
            synchronize_session="fetch",
        )
    )

    if updated_rows == 0:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Repository '{repo.owner}/{repo.name}' was claimed concurrently.",
        )

    # 6. Commit and refresh
    db.commit()
    db.refresh(repo)

    logger.info("Repository %s/%s successfully claimed by %s", repo.owner, repo.name, clean_handle)
    return repo


@router.post(
    "/maintainers/claim",
    response_model=ClaimRepoResponse,
    status_code=status.HTTP_200_OK,
    summary="Claim repository ownership",
)
@router.post(
    "/api/repos/claim",
    response_model=ClaimRepoResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
@router.post(
    "/api/maintainers/claim",
    response_model=ClaimRepoResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def handle_claim_repository(
    request: Request,
    payload: ClaimRepoRequest,
    db: Session = Depends(get_db),
):
    """
    Maintainer claims an unclaimed repository.

    - Validates repo existence in DB or live GitHub.
    - Rejects already-claimed repositories with 409 Conflict.
    - Records maintainer handle, payout address, and claim timestamp.
    - Returns updated repository details and copy-pasteable badge snippets.
    """
    target_name = payload.name or payload.repo
    repo = await claim_repository(
        db=db,
        owner=payload.owner,
        name=target_name,
        maintainer_handle=payload.maintainer_handle,
        payout_address=payload.payout_address,
    )

    base_url = resolve_base_url(request)
    snippets = generate_badge_snippets(repo.owner, repo.name, repo.id, base_url)
    repo_dto = RepoResponse.model_validate(repo)

    return ClaimRepoResponse(
        message=f"Repository '{repo.owner}/{repo.name}' successfully claimed by '{repo.maintainer_handle}'.",
        id=repo.id,
        owner=repo.owner,
        name=repo.name,
        description=repo.description,
        stars=repo.stars,
        primary_language=repo.primary_language,
        ci_status=repo.ci_status,
        claimed=repo.claimed,
        claimed_by=repo.claimed_by,
        maintainer_handle=repo.maintainer_handle,
        claimed_at=repo.claimed_at,
        payout_address=repo.payout_address,
        created_at=repo.created_at,
        updated_at=repo.updated_at,
        repository=repo_dto,
        snippets=snippets,
    )


@router.get(
    "/maintainers/claim",
    summary="Repository claim onboarding info",
)
async def get_claim_onboarding_info(
    repo: str | None = Query(None, description="Repository identifier as 'owner/repo'"),
    owner: str | None = Query(None),
    name: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """
    Informational endpoint for maintainers following onboarding links in SVG badges.
    """
    target_owner = owner
    target_name = name
    if repo and "/" in repo:
        parts = repo.split("/", 1)
        target_owner = target_owner or parts[0]
        target_name = target_name or parts[1]

    if not target_owner or not target_name:
        return {
            "status": "ready",
            "message": "Send a POST request to /maintainers/claim with owner, repo/name, and maintainer_handle.",
        }

    existing = (
        db.query(Repository)
        .filter(
            func.lower(Repository.owner) == target_owner.lower(),
            func.lower(Repository.name) == target_name.lower(),
        )
        .first()
    )

    return {
        "owner": target_owner,
        "name": target_name,
        "found_in_registry": existing is not None,
        "claimed": existing.claimed if existing else False,
        "instructions": "Send POST /maintainers/claim with JSON payload: {'owner': ..., 'name': ..., 'maintainer_handle': ..., 'payout_address': ...}",
    }


@router.get(
    "/maintainers/snippet/{owner}/{repo}",
    response_model=SnippetResponse,
    summary="Generate copy-pasteable badge snippets for repository",
    response_description="Badge snippets in Markdown, HTML, and RST formats",
)
@router.get(
    "/api/repos/{owner}/{repo}/snippet",
    response_model=SnippetResponse,
    summary="Alias: Generate copy-pasteable badge snippets for repository",
    response_description="Badge snippets in Markdown, HTML, and RST formats",
)
async def get_repository_badge_snippet(
    owner: str = Path(..., description="Repository owner or organization"),
    repo: str = Path(..., description="Repository name"),
    request: Request = None,
    db: Session = Depends(get_db),
):
    """
    Generate copy-pasteable badge snippets (Markdown, HTML, RST) for a repository.

    - Resolves repository by owner and name (case-insensitive, URL-safe).
    - Hydrates from GitHub REST API v3 if unindexed.
    - Links click URL dynamically to `/click/active/{repo.id}`.
    - Returns 404 if repository is not found on GitHub or platform registry.
    """
    clean_owner = urllib.parse.unquote(owner).strip()
    clean_repo = urllib.parse.unquote(repo).strip()

    if not clean_owner or not clean_repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository owner and name must not be empty.",
        )

    # Case-insensitive repository lookup
    repository = (
        db.query(Repository)
        .filter(
            func.lower(Repository.owner) == clean_owner.lower(),
            func.lower(Repository.name) == clean_repo.lower(),
        )
        .first()
    )

    if repository is None:
        repository = await get_or_fetch_repository(db, clean_owner, clean_repo)

    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{clean_owner}/{clean_repo}' not found.",
        )

    base_url = resolve_base_url(request)
    snippets = generate_badge_snippets(
        owner=repository.owner,
        name=repository.name,
        repo_id=repository.id,
        base_url=base_url,
    )

    return SnippetResponse(
        owner=repository.owner,
        name=repository.name,
        markdown=snippets["markdown"],
        html=snippets["html"],
        rst=snippets["rst"],
        badge_url=snippets["badge_url"],
        click_url=snippets["click_url"],
    )
