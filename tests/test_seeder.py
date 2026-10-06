"""
tests/test_seeder.py - Real GitHub Search API Seeder & Sponsor Ads Unit Tests
"""

import os
import tempfile
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_engine_with_pragmas
from app.models.ad import Ad
from app.models.repository import Repository
from scripts.seed_sample_ads import seed_sample_ads
from scripts.seed_top_repos import seed_top_repositories

# --- Fixtures ---

@pytest.fixture(scope="function")
def seeder_db():
    """Provides an isolated database session for seeder testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        temp_path = tf.name

    db_url = f"sqlite:///{temp_path}"
    engine = get_engine_with_pragmas(db_url)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()

    yield session

    session.close()
    engine.dispose()
    if os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except PermissionError:
            pass


@pytest.fixture
def mock_github_search_payload():
    """Authentic GitHub REST API v3 search response format."""
    return {
        "total_count": 2,
        "incomplete_results": False,
        "items": [
            {
                "id": 160875488,
                "name": "fastapi",
                "full_name": "tiangolo/fastapi",
                "owner": {"login": "tiangolo", "id": 1326112},
                "description": "FastAPI framework, high performance, easy to learn, fast to code, ready for production",
                "stargazers_count": 78200,
                "language": "Python",
                "fork": False,
            },
            {
                "id": 108110,
                "name": "django",
                "full_name": "django/django",
                "owner": {"login": "django", "id": 27804},
                "description": "The Web framework for perfectionists with deadlines.",
                "stargazers_count": 79500,
                "language": "Python",
                "fork": False,
            },
        ],
    }


# --- Area 1: Top Repositories Seeder Tests ---

def test_seed_top_repos_schema_ingestion(seeder_db, mock_github_search_payload, monkeypatch):
    """Verify seeder queries GitHub API and ingests authentic schema fields into DB."""
    def mock_get(url, **kwargs):
        return httpx.Response(200, json=mock_github_search_payload)

    monkeypatch.setattr(httpx, "get", mock_get)

    count_inserted = seed_top_repositories(seeder_db, languages=["Python"], limit_per_lang=2)
    assert count_inserted == 2

    repos = seeder_db.scalars(select(Repository).order_by(Repository.stars.desc())).all()
    assert len(repos) == 2

    django_repo = repos[0]
    assert django_repo.owner == "django"
    assert django_repo.name == "django"
    assert django_repo.stars == 79500
    assert django_repo.primary_language == "Python"
    assert django_repo.github_id == 108110
    assert django_repo.claimed is False
    assert django_repo.claimed_by is None, "Must be honestly None, never a synthetic persona"

    fastapi_repo = repos[1]
    assert fastapi_repo.owner == "tiangolo"
    assert fastapi_repo.name == "fastapi"
    assert fastapi_repo.stars == 78200


def test_seed_top_repos_idempotency(seeder_db, mock_github_search_payload, monkeypatch):
    """Verify running seeder multiple times updates or ignores without duplicating rows."""
    def mock_get(url, **kwargs):
        return httpx.Response(200, json=mock_github_search_payload)

    monkeypatch.setattr(httpx, "get", mock_get)

    first_run = seed_top_repositories(seeder_db, languages=["Python"], limit_per_lang=2)
    second_run = seed_top_repositories(seeder_db, languages=["Python"], limit_per_lang=2)

    total_rows = seeder_db.scalar(select(func.count(Repository.id)))
    assert total_rows == 2, f"Idempotency violation: expected 2 rows, found {total_rows}"


def test_seed_top_repos_multi_language(seeder_db, monkeypatch):
    """Verify seeder queries across Python, TypeScript, Rust, and Go."""
    lang_payloads = {
        "Python": [{"id": 1, "name": "p1", "owner": {"login": "o1"}, "stargazers_count": 100, "language": "Python"}],
        "TypeScript": [{"id": 2, "name": "t1", "owner": {"login": "o2"}, "stargazers_count": 200, "language": "TypeScript"}],
        "Rust": [{"id": 3, "name": "r1", "owner": {"login": "o3"}, "stargazers_count": 300, "language": "Rust"}],
        "Go": [{"id": 4, "name": "g1", "owner": {"login": "o4"}, "stargazers_count": 400, "language": "Go"}],
    }

    def mock_get(url, **kwargs):
        params = kwargs.get("params", {})
        q = str(params.get("q", ""))
        for lang, items in lang_payloads.items():
            if f"language:{lang.lower()}" in q.lower() or f"language:{lang.lower()}" in url.lower():
                return httpx.Response(200, json={"items": items})
        return httpx.Response(200, json={"items": []})

    monkeypatch.setattr(httpx, "get", mock_get)

    inserted = seed_top_repositories(seeder_db, languages=["Python", "TypeScript", "Rust", "Go"], limit_per_lang=1)
    repos = seeder_db.scalars(select(Repository)).all()

    languages_in_db = {r.primary_language for r in repos}
    assert {"Python", "TypeScript", "Rust", "Go"}.issubset(languages_in_db)


def test_seed_top_repos_rate_limit_transparent_error(seeder_db, monkeypatch):
    """Verify GitHub 403 Rate Limit is handled transparently without injecting fake data."""
    def mock_get(url, **kwargs):
        return httpx.Response(
            403,
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1727900000"},
            json={"message": "API rate limit exceeded", "documentation_url": "https://docs.github.com/rest/overview/resources-in-the-rest-api#rate-limiting"},
        )

    monkeypatch.setattr(httpx, "get", mock_get)

    inserted = seed_top_repositories(seeder_db, languages=["Python"])
    assert inserted == 0

    total_repos = seeder_db.scalar(select(func.count(Repository.id)))
    assert total_repos == 0, "No synthetic mock repositories may be inserted on rate-limit error"


def test_seed_top_repos_network_failure_handling(seeder_db, monkeypatch):
    """Verify network disconnect is handled transparently with zero synthetic data."""
    def mock_get(url, **kwargs):
        raise httpx.ConnectError("Network unreachable")

    monkeypatch.setattr(httpx, "get", mock_get)

    inserted = seed_top_repositories(seeder_db, languages=["Python"])
    assert inserted == 0

    total_repos = seeder_db.scalar(select(func.count(Repository.id)))
    assert total_repos == 0, "No synthetic mock data may be inserted on network failure"


# --- Area 2: Tech Sponsor Ads Seeder Tests ---

def test_seed_sample_ads_ingestion(seeder_db):
    """Verify authentic sponsor campaigns are seeded into the database."""
    count = seed_sample_ads(seeder_db)
    assert count >= 5

    ads = seeder_db.scalars(select(Ad)).all()
    sponsors = {ad.sponsor_name for ad in ads}
    expected_sponsors = {"Sentry", "Neon", "Supabase", "Docker", "GitHub Sponsors"}
    assert expected_sponsors.issubset(sponsors)


def test_seed_sample_ads_valid_attributes(seeder_db):
    """Verify seeded ads comply with financial constraints, valid URLs, and active status."""
    seed_sample_ads(seeder_db)

    ads = seeder_db.scalars(select(Ad)).all()
    has_universal = False

    for ad in ads:
        assert ad.is_active is True
        assert isinstance(ad.total_budget, Decimal) and ad.total_budget > Decimal("0")
        assert isinstance(ad.remaining_budget, Decimal) and ad.remaining_budget > Decimal("0")
        assert isinstance(ad.cost_per_click, Decimal) and ad.cost_per_click > Decimal("0")
        assert ad.click_url.startswith("https://"), f"Ad {ad.sponsor_name} has invalid click URL: {ad.click_url}"
        assert len(ad.headline.strip()) > 0
        assert len(ad.call_to_action.strip()) > 0

        if ad.target_language is None:
            has_universal = True

    assert has_universal, "Must seed at least one universal/fallback sponsor (target_language=None)"


def test_seed_sample_ads_idempotency(seeder_db):
    """Verify running sample ad seeder multiple times does not produce duplicate campaigns."""
    run1 = seed_sample_ads(seeder_db)
    run2 = seed_sample_ads(seeder_db)

    total_ads = seeder_db.scalar(select(func.count(Ad.id)))
    assert total_ads == run1, f"Idempotency violation: expected {run1} ads, found {total_ads}"
