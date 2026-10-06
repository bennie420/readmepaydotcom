"""Unit Tests for GitHub Client Service & In-Memory TTL Caching."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.orm import Session

from app.models.repository import Repository
from app.services.github_service import (
    clear_github_cache,
    fetch_ci_status,
    fetch_repository_metadata,
    get_or_fetch_repository,
    get_or_fetch_repository_data,
)
from tests.conftest import create_test_repository


@pytest.fixture(autouse=True)
def clean_cache():
    """Ensure in-memory TTL cache is clean between tests."""
    clear_github_cache()
    yield
    clear_github_cache()


@pytest.mark.asyncio
async def test_fetch_metadata_live_or_mocked():
    """Queries GitHub REST API /repos/{owner}/{repo} and extracts stars & language."""
    mock_payload = {
        "id": 123456,
        "name": "flask",
        "owner": {"login": "pallets"},
        "stargazers_count": 68000,
        "language": "Python",
        "description": "The Python micro framework",
    }
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = httpx.Response(200, json=mock_payload, headers={"x-ratelimit-remaining": "59"})
        data = await fetch_repository_metadata("pallets", "flask")
        assert data is not None
        assert data["stars"] == 68000
        assert data.stars == 68000
        assert data["primary_language"] == "Python"
        assert data.primary_language == "Python"
        assert data["github_id"] == 123456


@pytest.mark.asyncio
async def test_fetch_ci_status_workflow_runs():
    """Queries /actions/runs?per_page=1 and maps conclusion to CI status."""
    mock_runs = {
        "workflow_runs": [
            {"conclusion": "success", "status": "completed"}
        ]
    }
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = httpx.Response(200, json=mock_runs, headers={"x-ratelimit-remaining": "58"})
        status = await fetch_ci_status("pallets", "flask")
        assert status == "passing"


@pytest.mark.asyncio
async def test_ttl_cache_idempotency_avoids_repeated_requests():
    """Subsequent queries for same owner/repo return from in-memory cache within TTL."""
    mock_payload = {
        "id": 789,
        "name": "fastapi",
        "owner": {"login": "tiangolo"},
        "stargazers_count": 75000,
        "language": "Python",
        "description": "FastAPI framework",
    }
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = httpx.Response(200, json=mock_payload, headers={"x-ratelimit-remaining": "57"})

        # 1st call hits external API
        res1 = await get_or_fetch_repository_data("tiangolo", "fastapi")
        assert mock_get.call_count >= 1
        call_count_1 = mock_get.call_count

        # 2nd call hits in-memory TTL cache
        res2 = await get_or_fetch_repository_data("tiangolo", "fastapi")
        assert mock_get.call_count == call_count_1  # No additional network calls
        assert res1 == res2


@pytest.mark.asyncio
async def test_nonexistent_repo_returns_none_zero_synthetic_personas():
    """HTTP 404 from GitHub returns None honestly; zero synthetic mock records injected."""
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = httpx.Response(404, json={"message": "Not Found"})
        res = await fetch_repository_metadata("ghost-user", "nonexistent-repo")
        assert res is None


@pytest.mark.asyncio
async def test_rate_limit_403_handling():
    """HTTP 403 Rate Limit returns None transparently without crashing."""
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={"x-ratelimit-remaining": "0"},
        )
        res = await fetch_repository_metadata("heavy-user", "heavy-repo")
        assert res is None


@pytest.mark.asyncio
async def test_get_or_fetch_repository_db_integration(db_session: Session):
    """Local database lookup takes priority and avoids external GitHub API calls."""
    # Pre-seed repository in SQLite
    repo = create_test_repository(db_session, owner="localorg", name="localrepo", stars=123)

    # Should find in database with 0 external network requests
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        found = await get_or_fetch_repository(db_session, "localorg", "localrepo")
        assert found is not None
        assert found.id == repo.id
        assert found.stars == 123
        assert mock_get.call_count == 0


@pytest.mark.asyncio
async def test_get_or_fetch_repository_persists_new_repo(db_session: Session):
    """When repository is not in DB but found on GitHub, it is persisted to SQLite."""
    mock_payload = {
        "id": 998877,
        "name": "newrepo",
        "owner": {"login": "newowner"},
        "stargazers_count": 500,
        "language": "Go",
        "description": "A new repository",
    }
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = httpx.Response(200, json=mock_payload, headers={"x-ratelimit-remaining": "50"})
        new_repo = await get_or_fetch_repository(db_session, "newowner", "newrepo")
        assert new_repo is not None
        assert new_repo.name == "newrepo"
        assert new_repo.owner == "newowner"
        assert new_repo.stars == 500
        assert new_repo.primary_language == "Go"

        # Verify it was committed to DB
        reloaded = db_session.query(Repository).filter_by(owner="newowner", name="newrepo").first()
        assert reloaded is not None
        assert reloaded.id == new_repo.id
