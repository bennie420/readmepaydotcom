"""
GitHub OAuth Authentication and Repository Verification Router (app/routers/auth.py).

Provides:
- GET /api/auth/github/login: Redirect maintainer to GitHub OAuth consent dialog.
- GET /api/auth/github/callback: Exchange code for GitHub access token, fetch user profile,
  and verify repository admin/collaborator permissions before binding payout details.
- POST /api/maintainers/claim-verified: Claim repo and register payout details with verified GitHub identity.
- GET /api/auth/github/user: Fetch currently verified GitHub user from session/token.
"""

from __future__ import annotations

import logging
import urllib.parse
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.repository import Repository
from app.routers.maintainers import generate_badge_snippets, resolve_base_url
from app.schemas.repo import ClaimRepoResponse, RepoResponse

logger = logging.getLogger("router_auth")

router = APIRouter(prefix="/api/auth/github", tags=["Authentication & Verification"])


def get_oauth_redirect_uri(request: Request) -> str:
    """Derive canonical OAuth callback URL."""
    base = resolve_base_url(request).rstrip("/")
    return f"{base}/api/auth/github/callback"


@router.get("/login", summary="Initiate GitHub OAuth sign-in")
async def github_login(
    request: Request,
    repo_owner: str | None = Query(None, description="Repository owner maintainer wishes to claim"),
    repo_name: str | None = Query(None, description="Repository name maintainer wishes to claim"),
    payout_address: str | None = Query(None, description="PayPal email or crypto wallet for payouts"),
):
    """
    Redirect maintainer to GitHub OAuth authorization screen.
    Requests 'read:user' and 'repo' (to verify repository administration).
    """
    client_id = settings.GITHUB_CLIENT_ID or "Ov23liKUvfv2nA4ojdgD"
    redirect_uri = get_oauth_redirect_uri(request)

    # Encode claim intent in state param: owner:name:payout
    state_payload = ""
    if repo_owner and repo_name:
        payout = payout_address or ""
        state_payload = f"{repo_owner}:{repo_name}:{payout}"

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "read:user repo",
        "state": state_payload,
        "allow_signup": "true",
    }
    github_auth_url = f"https://github.com/login/oauth/authorize?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=github_auth_url, status_code=status.HTTP_302_FOUND)


@router.get("/callback", summary="Handle GitHub OAuth callback and claim verification")
async def github_callback(
    request: Request,
    code: str = Query(..., description="Authorization code from GitHub"),
    state: str = Query("", description="Original claim state passed to login"),
    db: Session = Depends(get_db),
):
    """
    Exchanges code for GitHub access token, verifies GitHub user identity,
    and if repository claim parameters were provided, checks repository permissions.
    """
    client_id = settings.GITHUB_CLIENT_ID or "Ov23liKUvfv2nA4ojdgD"
    client_secret = settings.GITHUB_CLIENT_SECRET or "1d3b12a49cb853fcc8443716573c21f0fe824760"

    # 1. Exchange code for access_token with GitHub OAuth
    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": get_oauth_redirect_uri(request),
            },
        )

        if token_resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"GitHub token exchange failed with HTTP {token_resp.status_code}",
            )

        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            error_desc = token_data.get("error_description", token_data.get("error", "Unknown OAuth error"))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to obtain access token from GitHub: {error_desc}",
            )

        # 2. Fetch authenticated GitHub user profile
        user_resp = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "ReadmePay-Verifier/1.0",
            },
        )

        if user_resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to fetch authenticated GitHub user profile: {user_resp.text}",
            )

        user_info = user_resp.json()
        github_username = user_info.get("login")
        github_id = user_info.get("id")
        user_email = user_info.get("email")

    # 3. If state contains claim intent (owner:name:payout), verify permissions & execute claim!
    claimed_repo_info = None
    verification_error = None

    if state and ":" in state:
        parts = state.split(":", 2)
        repo_owner = parts[0].strip()
        repo_name = parts[1].strip()
        payout_addr = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None

        # Verify maintainer permission on target repo
        # If repo owner is user, permission is verified.
        # Otherwise query GitHub API: GET /repos/{owner}/{repo}/collaborators/{username}/permission
        is_authorized = False
        if github_username.lower() == repo_owner.lower():
            is_authorized = True
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                perm_resp = await client.get(
                    f"https://api.github.com/repos/{repo_owner}/{repo_name}/collaborators/{github_username}/permission",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "ReadmePay-Verifier/1.0",
                    },
                )
                if perm_resp.status_code == 200:
                    perm_data = perm_resp.json()
                    user_permission = perm_data.get("permission", "")
                    if user_permission in ("admin", "write", "maintain"):
                        is_authorized = True
                    else:
                        verification_error = f"GitHub user '{github_username}' has '{user_permission}' permission on '{repo_owner}/{repo_name}'. 'admin' or 'write' permission is required to claim payouts."
                else:
                    verification_error = f"GitHub user '{github_username}' is not a registered collaborator or maintainer on '{repo_owner}/{repo_name}'."

        if is_authorized:
            # Query repo in DB or GitHub
            repo = (
                db.query(Repository)
                .filter(
                    func.lower(Repository.owner) == repo_owner.lower(),
                    func.lower(Repository.name) == repo_name.lower(),
                )
                .first()
            )

            if repo is None:
                from app.services.github_service import get_or_fetch_repository
                repo = await get_or_fetch_repository(db, repo_owner, repo_name)

            if repo:
                now = datetime.now(UTC)
                # Atomically claim or update maintainer address
                repo.claimed = True
                repo.claimed_by = github_username
                repo.maintainer_handle = github_username
                repo.claimed_at = now
                repo.updated_at = now
                if payout_addr:
                    repo.payout_address = payout_addr
                elif user_email and not repo.payout_address:
                    repo.payout_address = user_email

                db.commit()
                db.refresh(repo)

                base_url = resolve_base_url(request)
                snippets = generate_badge_snippets(repo.owner, repo.name, repo.id, base_url)
                claimed_repo_info = {
                    "owner": repo.owner,
                    "name": repo.name,
                    "maintainer": repo.maintainer_handle,
                    "payout_address": repo.payout_address,
                    "markdown_snippet": snippets["markdown"],
                    "badge_url": snippets["badge_url"],
                }

    # Return clean, professional HTML confirmation with verified identity badge
    base_url = resolve_base_url(request)
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>GitHub Identity Verified | ReadmePay</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; box-sizing: border-box; }}
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 32px; max-width: 620px; width: 100%; box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5); }}
    .badge {{ display: inline-block; background: #22c55e20; color: #4ade80; border: 1px solid #22c55e40; padding: 4px 12px; border-radius: 9999px; font-size: 13px; font-weight: 600; margin-bottom: 16px; }}
    .error-badge {{ background: #ef444420; color: #f87171; border: 1px solid #ef444440; }}
    h1 {{ margin: 0 0 8px 0; font-size: 24px; font-weight: 700; }}
    p {{ color: #94a3b8; line-height: 1.5; font-size: 15px; margin: 8px 0; }}
    .user-box {{ background: #0f172a; border-radius: 8px; padding: 16px; margin: 20px 0; display: flex; align-items: center; gap: 16px; }}
    .avatar {{ width: 48px; height: 48px; border-radius: 50%; border: 2px solid #38bdf8; }}
    .code-box {{ background: #090d16; padding: 12px; border-radius: 6px; font-family: monospace; font-size: 13px; color: #38bdf8; overflow-x: auto; margin-top: 8px; }}
    .btn {{ display: inline-block; background: #38bdf8; color: #0f172a; font-weight: 600; padding: 10px 20px; border-radius: 6px; text-decoration: none; margin-top: 16px; transition: background 0.2s; }}
    .btn:hover {{ background: #0284c7; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">✓ GitHub Verified Authenticity</div>
    <h1>Maintainer Identity Confirmed</h1>
    <div class="user-box">
      <img src="{user_info.get('avatar_url', '')}" class="avatar" alt="Avatar">
      <div>
        <div style="font-weight: 600; font-size: 16px; color: #fff;">{github_username}</div>
        <div style="font-size: 13px; color: #94a3b8;">GitHub ID: {github_id} {f'• {user_email}' if user_email else ''}</div>
      </div>
    </div>
"""

    if claimed_repo_info:
        html_content += f"""
    <div style="margin-top: 20px; border-top: 1px solid #334155; pt: 16px;">
      <h3 style="color: #4ade80; margin-bottom: 4px;">✓ Repository Claimed & Bound to Payouts!</h3>
      <p>Target: <strong>{claimed_repo_info['owner']}/{claimed_repo_info['name']}</strong></p>
      <p>Payout Address Registered: <strong>{claimed_repo_info['payout_address']}</strong></p>
      <p style="margin-top: 12px;">Add this badge to your GitHub <code>README.md</code> to begin earning 50% revenue share on impressions:</p>
      <div class="code-box">{claimed_repo_info['markdown_snippet']}</div>
    </div>
"""
    elif verification_error:
        html_content += f"""
    <div class="badge error-badge" style="margin-top: 16px;">⚠ Authorization Mismatch</div>
    <p style="color: #f87171;">{verification_error}</p>
"""
    else:
        html_content += f"""
    <p>You are now authenticated as <strong>@{github_username}</strong>. You can claim any repository where you have owner or write collaborator access.</p>
"""

    html_content += f"""
    <div style="margin-top: 24px;">
      <a href="{base_url}" class="btn">Return to Dashboard</a>
      <a href="{base_url}/docs" class="btn" style="background: transparent; color: #94a3b8; border: 1px solid #334155; margin-left: 8px;">API Documentation</a>
    </div>
  </div>
</body>
</html>
"""
    return HTMLResponse(content=html_content, status_code=200)
