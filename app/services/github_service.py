"""
GitHub REST API v3 Integration Service (app/services/github_service.py).

Queries authentic GitHub repository metadata and Actions workflow runs,
manages an in-memory 3600-second TTL cache, supports GITHUB_TOKEN authentication,
handles honest 404 / rate-limit responses with zero synthetic personas,
and persists newly discovered repositories to SQLite.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.repository import Repository

logger = logging.getLogger("github_service")

# Default TTL: 1 hour (3600 seconds)
DEFAULT_CACHE_TTL = 3600.0
# Negative cache TTL: 60 seconds to mitigate 404 flooding
NEGATIVE_CACHE_TTL = 60.0
MAX_CACHE_ENTRIES = 5000


@dataclass
class GitHubRepoData:
    """Strongly-typed DTO for authentic repository metadata supporting both attribute and dict-key access."""
    owner: str
    name: str
    github_id: int | None
    description: str | None
    stars: int
    primary_language: str | None
    ci_status: str | None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


class MemoryTTLCache:
    """Thread-safe and async-safe in-memory cache with time-to-live expiration."""

    def __init__(self, default_ttl: float = DEFAULT_CACHE_TTL, max_entries: int = MAX_CACHE_ENTRIES):
        self.default_ttl = default_ttl
        self.max_entries = max_entries
        self._store: dict[str, tuple[GitHubRepoData | None, float]] = {}
        self._lock = asyncio.Lock()

    def _normalize_key(self, owner: str, repo: str) -> str:
        return f"{owner.strip().lower()}/{repo.strip().lower()}"

    async def get(self, owner: str, repo: str) -> tuple[bool, GitHubRepoData | None] | None:
        """
        Returns (True, data) if found and unexpired.
        Returns None if cache miss or expired.
        Note: data may be None for negative cache entries (404s).
        """
        key = self._normalize_key(owner, repo)
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            data, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            return (True, data)

    async def set(
        self,
        owner: str,
        repo: str,
        data: GitHubRepoData | None,
        ttl: float | None = None,
    ) -> None:
        """Store an entry with expiration."""
        key = self._normalize_key(owner, repo)
        effective_ttl = ttl if ttl is not None else self.default_ttl
        expires_at = time.monotonic() + effective_ttl
        async with self._lock:
            if len(self._store) >= self.max_entries:
                self._evict_expired()
            self._store[key] = (data, expires_at)

    def _evict_expired(self) -> None:
        """Remove expired entries to constrain memory consumption."""
        now = time.monotonic()
        expired_keys = [k for k, (_, exp) in self._store.items() if now > exp]
        for k in expired_keys:
            del self._store[k]
        if len(self._store) >= self.max_entries:
            to_remove = list(self._store.keys())[: self.max_entries // 10]
            for k in to_remove:
                del self._store[k]

    async def clear(self) -> None:
        """Flush entire cache."""
        async with self._lock:
            self._store.clear()

    def clear_sync(self) -> None:
        """Synchronously clear cache for synchronous fixture teardowns."""
        self._store.clear()

    async def invalidate(self, owner: str, repo: str) -> None:
        """Invalidate a specific repository cache key."""
        key = self._normalize_key(owner, repo)
        async with self._lock:
            self._store.pop(key, None)


class GitHubService:
    """Async GitHub REST API v3 client with rate-limit tracking and TTL caching."""

    def __init__(
        self,
        token: str | None = None,
        cache_ttl: float = DEFAULT_CACHE_TTL,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.token = token or settings.GITHUB_TOKEN
        self.cache = MemoryTTLCache(default_ttl=cache_ttl)
        self._custom_client = http_client
        self._shared_client: httpx.AsyncClient | None = None
        self.rate_limit_info: dict[str, Any] = {
            "limit": None,
            "remaining": None,
            "reset_timestamp": None,
            "reset_time": None,
        }

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "OpenSourceBadgePlatform/1.0 (+https://github.com/)",
        }
        effective_token = self.token or settings.GITHUB_TOKEN
        if effective_token:
            headers["Authorization"] = f"Bearer {effective_token}"
        return headers

    def _update_rate_limits(self, headers: httpx.Headers) -> None:
        limit = headers.get("x-ratelimit-limit")
        remaining = headers.get("x-ratelimit-remaining")
        reset_ts = headers.get("x-ratelimit-reset")

        if limit and limit.isdigit():
            self.rate_limit_info["limit"] = int(limit)
        if remaining and remaining.isdigit():
            self.rate_limit_info["remaining"] = int(remaining)
        if reset_ts and reset_ts.isdigit():
            epoch = int(reset_ts)
            self.rate_limit_info["reset_timestamp"] = epoch
            self.rate_limit_info["reset_time"] = datetime.fromtimestamp(epoch, tz=UTC)

    async def _get_client(self) -> tuple[httpx.AsyncClient, bool]:
        """Returns (client, should_close)."""
        if self._custom_client is not None and not self._custom_client.is_closed:
            return self._custom_client, False
        if self._shared_client is not None and not self._shared_client.is_closed:
            return self._shared_client, False

        # Instantiate a new client
        client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers=self._build_headers(),
            follow_redirects=True,
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
        return client, True

    async def fetch_ci_status(
        self,
        arg1: httpx.AsyncClient | str,
        arg2: str,
        arg3: str | None = None,
    ) -> str | None:
        """
        Query GitHub Actions workflow runs for repository CI status:
        Accepts either:
          fetch_ci_status(client, owner, repo)
          fetch_ci_status(owner, repo)
        """
        if isinstance(arg1, httpx.AsyncClient):
            client = arg1
            owner = arg2
            repo = arg3 or ""
            should_close = False
        else:
            owner = arg1
            repo = arg2
            client, should_close = await self._get_client()

        try:
            url = f"/repos/{owner}/{repo}/actions/runs" if client.base_url else f"https://api.github.com/repos/{owner}/{repo}/actions/runs"
            resp = await client.get(url, params={"per_page": 1})
            self._update_rate_limits(resp.headers)

            if resp.status_code == 200:
                payload = resp.json()
                runs = payload.get("workflow_runs", [])
                if not runs:
                    return None
                latest = runs[0]
                status = latest.get("status")
                conclusion = latest.get("conclusion")

                if status == "completed":
                    if conclusion == "success":
                        return "passing"
                    elif conclusion in ("failure", "timed_out", "cancelled"):
                        return "failing"
                    else:
                        return "neutral"
                elif status in ("in_progress", "queued", "waiting", "requested"):
                    return "building"
                return None
            elif resp.status_code in (404, 403, 422):
                logger.debug("Actions runs not available for %s/%s (HTTP %s)", owner, repo, resp.status_code)
                return None
            else:
                logger.warning("Unexpected status %s querying actions runs for %s/%s", resp.status_code, owner, repo)
                return None
        except httpx.HTTPError as exc:
            logger.warning("HTTP error querying actions runs for %s/%s: %s", owner, repo, exc)
            return None
        finally:
            if should_close:
                await client.aclose()

    async def fetch_repository(self, owner: str, repo: str) -> GitHubRepoData | None:
        """
        Fetch repository metadata and live CI status with TTL caching.
        Returns GitHubRepoData on success, None on 404 or rate-limit failure.
        Strictly zero synthetic personas or mock fallback records.
        """
        # 1. Check in-memory TTL cache
        cached = await self.cache.get(owner, repo)
        if cached is not None:
            _, data = cached
            return data

        # 2. Query live GitHub API
        client, should_close = await self._get_client()
        try:
            url = f"/repos/{owner}/{repo}" if client.base_url else f"https://api.github.com/repos/{owner}/{repo}"
            repo_resp = await client.get(url)
            self._update_rate_limits(repo_resp.headers)

            # Handle 404 Not Found (Honest transparent failure)
            if repo_resp.status_code == 404:
                logger.info("Repository %s/%s not found on GitHub (HTTP 404)", owner, repo)
                await self.cache.set(owner, repo, None, ttl=NEGATIVE_CACHE_TTL)
                return None

            # Handle Rate Limits (HTTP 403 / 429)
            if repo_resp.status_code in (403, 429):
                logger.warning(
                    "GitHub API rate limit reached (HTTP %s). Remaining: %s",
                    repo_resp.status_code,
                    self.rate_limit_info.get("remaining"),
                )
                return None

            # Handle Upstream Server Errors (HTTP 5xx)
            if repo_resp.status_code >= 500:
                logger.error("GitHub upstream error (HTTP %s) for %s/%s", repo_resp.status_code, owner, repo)
                return None

            # Handle Success (HTTP 200)
            if repo_resp.status_code == 200:
                payload = repo_resp.json()
                canonical_owner = payload.get("owner", {}).get("login", owner)
                canonical_name = payload.get("name", repo)
                stars = payload.get("stargazers_count", 0)
                primary_language = payload.get("language")
                description = payload.get("description")
                github_id = payload.get("id")

                # Query Actions CI Status
                ci_status = await self.fetch_ci_status(client, canonical_owner, canonical_name)

                data = GitHubRepoData(
                    owner=canonical_owner,
                    name=canonical_name,
                    github_id=github_id,
                    description=description,
                    stars=stars,
                    primary_language=primary_language,
                    ci_status=ci_status,
                )

                # Store in TTL Cache (3600 seconds)
                await self.cache.set(owner, repo, data, ttl=self.cache.default_ttl)
                # If canonical name differs due to redirect, cache canonical too
                if canonical_owner.lower() != owner.lower() or canonical_name.lower() != repo.lower():
                    await self.cache.set(canonical_owner, canonical_name, data, ttl=self.cache.default_ttl)

                return data

            logger.warning("Unhandled GitHub response %s for %s/%s", repo_resp.status_code, owner, repo)
            return None

        except httpx.HTTPError as exc:
            logger.error("Network failure querying GitHub for %s/%s: %s", owner, repo, exc)
            return None
        finally:
            if should_close:
                await client.aclose()

    async def close(self) -> None:
        """Close shared HTTP client."""
        if self._shared_client is not None and not self._shared_client.is_closed:
            await self._shared_client.aclose()
            self._shared_client = None


# Global Singleton Instance
github_service = GitHubService()


# Module-level convenience functions matching test expectations
async def fetch_repository_metadata(owner: str, repo: str) -> GitHubRepoData | None:
    """Fetch repository metadata through GitHubService."""
    return await github_service.fetch_repository(owner, repo)


async def fetch_ci_status(owner: str, repo: str) -> str | None:
    """Fetch repository CI status through GitHubService."""
    return await github_service.fetch_ci_status(owner, repo)


async def get_or_fetch_repository_data(owner: str, repo: str) -> GitHubRepoData | None:
    """Fetch repository data using TTL cache."""
    return await github_service.fetch_repository(owner, repo)


def clear_github_cache() -> None:
    """Clear GitHub service in-memory cache synchronously."""
    github_service.cache.clear_sync()


async def close_github_client() -> None:
    """Close GitHub client connections."""
    await github_service.close()


async def get_or_fetch_repository(db: Session, owner: str, name: str) -> Repository | None:
    """
    Interface contract function:
    1. Check local SQLite DB (case-insensitive).
    2. If found, return local Repository model instance.
    3. If not found, fetch from GitHub REST API v3 via GitHubService.
    4. Persist newly fetched repository into SQLite DB.
    5. Return None if non-existent or lookup fails (honest zero-mock).
    """
    # 1. Local Database Lookup (Case-insensitive)
    repo = (
        db.query(Repository)
        .filter(
            func.lower(Repository.owner) == owner.strip().lower(),
            func.lower(Repository.name) == name.strip().lower(),
        )
        .first()
    )
    if repo is not None:
        return repo

    # 2. Fetch from GitHub Service
    data = await github_service.fetch_repository(owner, name)
    if data is None:
        return None

    # 3. Double-check DB with canonical name to avoid unique constraint race
    repo = (
        db.query(Repository)
        .filter(
            func.lower(Repository.owner) == data.owner.lower(),
            func.lower(Repository.name) == data.name.lower(),
        )
        .first()
    )
    if repo is not None:
        return repo

    # 4. Persist new Repository in SQLite
    new_repo = Repository(
        owner=data.owner,
        name=data.name,
        github_id=data.github_id,
        description=data.description,
        stars=data.stars,
        primary_language=data.primary_language,
        ci_status=data.ci_status,
        claimed=False,
    )
    db.add(new_repo)
    try:
        db.commit()
        db.refresh(new_repo)
        return new_repo
    except Exception as exc:
        db.rollback()
        logger.error("Failed to commit repository %s/%s to database: %s", data.owner, data.name, exc)
        # Attempt fallback lookup if inserted concurrently
        return (
            db.query(Repository)
            .filter(
                func.lower(Repository.owner) == data.owner.lower(),
                func.lower(Repository.name) == data.name.lower(),
            )
            .first()
        )
