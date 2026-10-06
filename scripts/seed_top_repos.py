"""
scripts/seed_top_repos.py - Real GitHub REST API Search Seeder

Discovers and registers top open-source repositories from the live GitHub Search API.
Adheres strictly to the Zero-Mock policy: transparently handles rate limits and network
failures without injecting synthetic mock records or fake personas.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

# Allow running directly from project root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import Base, SessionLocal, engine, init_db
from app.models.repository import Repository

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_top_repos")

GITHUB_SEARCH_API = "https://api.github.com/search/repositories"
DEFAULT_TARGET_LANGUAGES = ["Python", "TypeScript", "Rust", "Go"]
DEFAULT_MIN_STARS = 25000
DEFAULT_PER_LANG_LIMIT = 10


def parse_rate_limit_headers(headers: Any) -> Dict[str, Any]:
    """Extract and parse GitHub rate limit headers."""
    limit = headers.get("x-ratelimit-limit") or headers.get("X-RateLimit-Limit")
    remaining = headers.get("x-ratelimit-remaining") or headers.get("X-RateLimit-Remaining")
    reset_ts = headers.get("x-ratelimit-reset") or headers.get("X-RateLimit-Reset")
    retry_after = headers.get("retry-after") or headers.get("Retry-After")

    reset_time = None
    seconds_until_reset = 0
    if reset_ts and str(reset_ts).isdigit():
        reset_epoch = int(reset_ts)
        reset_time = datetime.fromtimestamp(reset_epoch, tz=timezone.utc)
        seconds_until_reset = max(0, reset_epoch - int(time.time()))

    return {
        "limit": int(limit) if limit and str(limit).isdigit() else None,
        "remaining": int(remaining) if remaining and str(remaining).isdigit() else None,
        "reset_timestamp": int(reset_ts) if reset_ts and str(reset_ts).isdigit() else None,
        "reset_time": reset_time,
        "seconds_until_reset": seconds_until_reset,
        "retry_after": int(retry_after) if retry_after and str(retry_after).isdigit() else None,
    }


def query_github_search(
    query: str,
    limit: int = 10,
    token: Optional[str] = None,
    wait_on_rate_limit: bool = False,
) -> Optional[List[Dict[str, Any]]]:
    """
    Execute a real HTTP query against GitHub Search API with transparent rate limit handling.
    Strictly returns authentic records or None upon rate limit/error (zero synthetic mocks).
    """
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "BadgePlatformSeeder/1.0 (OpenSourceBadgePlatform)",
    }
    auth_token = token or os.getenv("GITHUB_TOKEN")
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    params = {
        "q": query,
        "sort": "stars",
        "order": "desc",
        "per_page": min(limit, 100),
    }

    try:
        resp = httpx.get(GITHUB_SEARCH_API, headers=headers, params=params, timeout=15.0)

        rate_info = parse_rate_limit_headers(resp.headers)
        logger.debug(
            "Search query '%s' - HTTP %s (Rate Limit: %s remaining)",
            query,
            resp.status_code,
            rate_info.get("remaining"),
        )

        # Handle Rate Limiting (403 Forbidden or 429 Too Many Requests)
        if resp.status_code in (403, 429):
            wait_sec = rate_info["retry_after"] or rate_info["seconds_until_reset"] or 60
            reset_str = rate_info["reset_time"].isoformat() if rate_info["reset_time"] else "unknown"
            logger.warning(
                "GitHub API rate limit exceeded (HTTP %d). Limit: %s/min, Reset window: %s (%ds remaining).",
                resp.status_code,
                rate_info.get("limit", "10"),
                reset_str,
                wait_sec,
            )
            logger.warning(
                "Zero-Mock Enforcement: Aborting query '%s' without injecting fake personas or synthetic records.",
                query,
            )
            if not auth_token:
                logger.info("Hint: Set the GITHUB_TOKEN environment variable to increase rate limit.")

            if wait_on_rate_limit and wait_sec <= 70:
                logger.info("Sleeping for %d seconds until rate limit reset...", wait_sec + 2)
                time.sleep(wait_sec + 2)
                return query_github_search(query, limit, token, wait_on_rate_limit=False)

            return None

        if resp.status_code != 200:
            logger.error("GitHub API search error: HTTP %d - %s", resp.status_code, resp.text)
            return None

        data = resp.json()
        return data.get("items", [])

    except httpx.RequestError as exc:
        logger.error("Network connection error querying GitHub Search API: %s", exc)
        return None


def upsert_repository(db: Session, item: Dict[str, Any]) -> str:
    """
    Idempotently insert or update a genuine repository row in the database.
    Returns 'inserted', 'updated', or 'skipped'.
    """
    owner_data = item.get("owner", {})
    if isinstance(owner_data, dict):
        owner = owner_data.get("login")
    else:
        owner = str(owner_data)

    name = item.get("name")
    if not owner or not name:
        return "skipped"

    github_id = item.get("id")
    stars = int(item.get("stargazers_count", 0))
    primary_language = item.get("language")
    description = item.get("description")

    # Check existing by (owner, name)
    existing = db.scalars(select(Repository).filter_by(owner=owner, name=name)).first()
    now = datetime.now(timezone.utc)

    if existing:
        existing.stars = stars
        existing.primary_language = primary_language
        if description:
            existing.description = description
        if github_id and not existing.github_id:
            existing.github_id = github_id
        existing.updated_at = now
        return "updated"

    # New unclaimed repository
    new_repo = Repository(
        owner=owner,
        name=name,
        github_id=github_id,
        description=description,
        stars=stars,
        primary_language=primary_language,
        claimed=False,
        claimed_by=None,
        claimed_at=None,
        payout_address=None,
        created_at=now,
        updated_at=now,
    )
    db.add(new_repo)
    return "inserted"


def seed_top_repositories(
    db: Session,
    languages: Optional[List[str]] = None,
    limit_per_lang: int = DEFAULT_PER_LANG_LIMIT,
    min_stars: int = DEFAULT_MIN_STARS,
    token: Optional[str] = None,
    include_global: Optional[bool] = None,
    wait_on_rate_limit: bool = False,
) -> int:
    """
    Seed top repositories across multiple languages from the live GitHub Search API.
    Returns total count of repositories inserted or updated.
    """
    # If languages is explicitly given, do not include global queries unless explicitly requested
    do_include_global = (languages is None) if include_global is None else include_global
    target_languages = languages if languages is not None else DEFAULT_TARGET_LANGUAGES
    queries: List[tuple[str, str]] = []

    if do_include_global:
        queries.append(("Global Top", f"stars:>{min_stars} fork:false"))

    for lang in target_languages:
        queries.append((f"Lang: {lang}", f"language:{lang} stars:>{min_stars} fork:false"))

    total_inserted = 0
    total_updated = 0

    for label, query_str in queries:
        logger.info("Executing GitHub search query for %s: '%s'...", label, query_str)
        items = query_github_search(
            query=query_str,
            limit=limit_per_lang,
            token=token,
            wait_on_rate_limit=wait_on_rate_limit,
        )

        if items is None:
            logger.warning("No items returned for %s (rate limited or connection error).", label)
            continue

        query_inserted = 0
        query_updated = 0
        for item in items:
            outcome = upsert_repository(db, item)
            if outcome == "inserted":
                query_inserted += 1
            elif outcome == "updated":
                query_updated += 1

        db.commit()
        total_inserted += query_inserted
        total_updated += query_updated
        logger.info(
            "Query %s completed: %d newly inserted, %d updated.",
            label,
            query_inserted,
            query_updated,
        )

    logger.info(
        "Seeding run finished: %d total inserted, %d total updated.",
        total_inserted,
        total_updated,
    )
    return total_inserted + total_updated


# Alias for compatibility
seed_top_repos = seed_top_repositories


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed top GitHub repositories into database.")
    parser.add_argument(
        "--languages",
        type=str,
        default=",".join(DEFAULT_TARGET_LANGUAGES),
        help="Comma-separated languages (e.g. 'Python,TypeScript,Rust,Go')",
    )
    parser.add_argument(
        "--min-stars",
        type=int,
        default=DEFAULT_MIN_STARS,
        help=f"Minimum stars threshold (default {DEFAULT_MIN_STARS})",
    )
    parser.add_argument(
        "--limit-per-lang",
        type=int,
        default=DEFAULT_PER_LANG_LIMIT,
        help=f"Repositories to fetch per language (default {DEFAULT_PER_LANG_LIMIT})",
    )
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="GitHub Personal Access Token (defaults to GITHUB_TOKEN env var)",
    )
    parser.add_argument(
        "--wait-on-rate-limit",
        action="store_true",
        help="Wait until rate limit resets instead of aborting immediately",
    )
    parser.add_argument(
        "--no-global",
        action="store_true",
        help="Do not query global top repos across all languages",
    )

    args = parser.parse_args()
    langs = [lang.strip() for lang in args.languages.split(",") if lang.strip()]

    # Ensure tables exist
    init_db(engine)

    with SessionLocal() as db:
        count = seed_top_repositories(
            db=db,
            languages=langs,
            limit_per_lang=args.limit_per_lang,
            min_stars=args.min_stars,
            token=args.token,
            include_global=not args.no_global,
            wait_on_rate_limit=args.wait_on_rate_limit,
        )
        print(f"\n[OK] Repository Seeding Complete: {count} repositories processed.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
