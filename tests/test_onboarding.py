"""
Unit and Integration Tests for Maintainer Onboarding and Snippet Generation (Milestone 4).

Covers:
- POST /maintainers/claim (and aliases /api/repos/claim, /api/maintainers/claim)
- 409 Conflict on already claimed repositories
- Honest 404 on non-existent repositories
- Input validation (empty/whitespace handles, schema requirements)
- GET /maintainers/snippet/{owner}/{repo} (Markdown, HTML, RST formats)
- GET /maintainers/claim informational endpoint
"""

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from tests.conftest import create_test_repository


def test_claim_unclaimed_repo_success(client: TestClient, db_session: Session):
    """Verify claiming an unclaimed repository succeeds with 200 and sets fields."""
    repo = create_test_repository(db_session, owner="org-unit", name="repo-one", claimed=False)
    payload = {
        "owner": repo.owner,
        "name": repo.name,
        "maintainer_handle": "lead_coder",
        "payout_address": "0xABCDEF1234567890",
    }
    response = client.post("/maintainers/claim", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["claimed"] is True
    assert data["maintainer_handle"] == "lead_coder"
    assert data["payout_address"] == "0xABCDEF1234567890"
    assert "snippets" in data
    assert "markdown" in data["snippets"]
    assert "html" in data["snippets"]
    assert "rst" in data["snippets"]

    # Verify DB state
    db_session.refresh(repo)
    assert repo.claimed is True
    assert repo.maintainer_handle == "lead_coder"
    assert repo.payout_address == "0xABCDEF1234567890"
    assert repo.claimed_at is not None


def test_claim_already_claimed_repo_returns_409(client: TestClient, db_session: Session):
    """Verify attempting to claim an already claimed repository returns 409 Conflict."""
    repo = create_test_repository(
        db_session,
        owner="org-unit",
        name="repo-claimed",
        claimed=True,
        maintainer_handle="first_maintainer",
    )
    payload = {
        "owner": repo.owner,
        "name": repo.name,
        "maintainer_handle": "second_maintainer",
    }
    response = client.post("/maintainers/claim", json=payload)
    assert response.status_code == 409
    assert "already been claimed" in response.json()["detail"].lower()


def test_claim_missing_repo_returns_404(client: TestClient):
    """Verify claiming a non-existent repository returns authentic 404 Not Found."""
    payload = {
        "owner": "missing-org-xyz",
        "name": "missing-repo-xyz",
        "maintainer_handle": "ghost_maintainer",
    }
    response = client.post("/maintainers/claim", json=payload)
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_claim_with_repo_field_alias(client: TestClient, db_session: Session):
    """Verify payload using 'repo' instead of 'name' is accepted."""
    repo = create_test_repository(db_session, owner="alias-org", name="alias-repo", claimed=False)
    payload = {
        "owner": repo.owner,
        "repo": repo.name,
        "maintainer_handle": "alias_dev",
    }
    response = client.post("/maintainers/claim", json=payload)
    assert response.status_code == 200
    db_session.refresh(repo)
    assert repo.claimed is True
    assert repo.maintainer_handle == "alias_dev"


def test_claim_validation_empty_handle_returns_422(client: TestClient, db_session: Session):
    """Verify maintainer_handle cannot be an empty string."""
    repo = create_test_repository(db_session, claimed=False)
    payload = {
        "owner": repo.owner,
        "name": repo.name,
        "maintainer_handle": "",
    }
    response = client.post("/maintainers/claim", json=payload)
    assert response.status_code in [400, 422]


def test_claim_validation_whitespace_handle_returns_422(client: TestClient, db_session: Session):
    """Verify maintainer_handle cannot be whitespace-only."""
    repo = create_test_repository(db_session, claimed=False)
    payload = {
        "owner": repo.owner,
        "name": repo.name,
        "maintainer_handle": "     ",
    }
    response = client.post("/maintainers/claim", json=payload)
    assert response.status_code in [400, 422]


def test_claim_special_character_handle_succeeds(client: TestClient, db_session: Session):
    """Verify handle containing '@' symbol is preserved."""
    repo = create_test_repository(db_session, claimed=False)
    payload = {
        "owner": repo.owner,
        "name": repo.name,
        "maintainer_handle": "@octocat_maintainer",
    }
    response = client.post("/maintainers/claim", json=payload)
    assert response.status_code == 200
    db_session.refresh(repo)
    assert repo.maintainer_handle == "@octocat_maintainer"


def test_claim_api_aliases(client: TestClient, db_session: Session):
    """Verify alternative claim route aliases /api/repos/claim and /api/maintainers/claim."""
    repo1 = create_test_repository(db_session, owner="org1", name="repo-alias-1", claimed=False)
    res1 = client.post(
        "/api/repos/claim",
        json={"owner": repo1.owner, "name": repo1.name, "maintainer_handle": "dev1"},
    )
    assert res1.status_code == 200

    repo2 = create_test_repository(db_session, owner="org2", name="repo-alias-2", claimed=False)
    res2 = client.post(
        "/api/maintainers/claim",
        json={"owner": repo2.owner, "name": repo2.name, "maintainer_handle": "dev2"},
    )
    assert res2.status_code == 200


def test_snippet_generation_formats_and_urls(client: TestClient, db_session: Session):
    """Verify snippet generator endpoint produces Markdown, HTML, and RST pointing to correct endpoints."""
    repo = create_test_repository(db_session, owner="snippet-org", name="snippet-tool")
    response = client.get(f"/maintainers/snippet/{repo.owner}/{repo.name}")
    assert response.status_code == 200
    data = response.json()

    assert data["owner"] == "snippet-org"
    assert data["name"] == "snippet-tool"

    # Markdown format
    md = data["markdown"]
    assert md.startswith("[!") and md.endswith(")")
    assert f"/badge/{repo.owner}/{repo.name}.svg" in md
    assert f"/click/active/{repo.id}" in md

    # HTML format
    html = data["html"]
    assert f'<a href="http://testserver/click/active/{repo.id}">' in html
    assert f'<img src="http://testserver/badge/{repo.owner}/{repo.name}.svg"' in html

    # RST format
    rst = data["rst"]
    assert ".. image::" in rst
    assert f":target: http://testserver/click/active/{repo.id}" in rst

    # Direct URLs
    assert data["badge_url"] == f"http://testserver/badge/{repo.owner}/{repo.name}.svg"
    assert data["click_url"] == f"http://testserver/click/active/{repo.id}"


def test_snippet_endpoint_missing_repo_returns_404(client: TestClient):
    """Verify requesting snippet for unindexed repository returns 404."""
    response = client.get("/maintainers/snippet/nonexistent/tool")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_snippet_alias_endpoint(client: TestClient, db_session: Session):
    """Verify alias /api/repos/{owner}/{repo}/snippet returns identical snippets."""
    repo = create_test_repository(db_session, owner="alias-owner", name="alias-pkg")
    response = client.get(f"/api/repos/{repo.owner}/{repo.name}/snippet")
    assert response.status_code == 200
    data = response.json()
    assert "markdown" in data
    assert f"/badge/{repo.owner}/{repo.name}.svg" in data["markdown"]


def test_get_claim_onboarding_info(client: TestClient, db_session: Session):
    """Verify GET /maintainers/claim informational endpoint responds correctly."""
    repo = create_test_repository(db_session, owner="info-org", name="info-repo", claimed=False)

    # General info
    res_gen = client.get("/maintainers/claim")
    assert res_gen.status_code == 200
    assert res_gen.json()["status"] == "ready"

    # Repo query
    res_repo = client.get(f"/maintainers/claim?repo={repo.owner}/{repo.name}")
    assert res_repo.status_code == 200
    data = res_repo.json()
    assert data["found_in_registry"] is True
    assert data["claimed"] is False
