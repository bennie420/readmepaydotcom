"""Reusable test fixtures, ASGI client harness, and database session setup for E2E tests."""

import asyncio
import hashlib
import sys
import xml.etree.ElementTree as ET
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Safe conditional imports for progressive testability during parallel milestones
try:
    from app.database import Base, get_db
    from app.main import app
    from app.models.ad import Ad
    from app.models.analytics import Click, Impression
    from app.models.repository import Repository

    APP_AVAILABLE = True
except ImportError:
    app = None
    Base = None
    get_db = None
    Repository = None
    Ad = None
    Impression = None
    Click = None
    APP_AVAILABLE = False


# SQLite in-memory test database engine with foreign keys enforced
TEST_DATABASE_URL = "sqlite:///:memory:"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated, clean database session for each test."""
    if not APP_AVAILABLE or Base is None:
        pytest.skip("Application models not yet available")
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """Synchronous FastAPI TestClient with database session override."""
    if not APP_AVAILABLE or app is None:
        pytest.skip("FastAPI application app.main not yet available")

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, base_url="http://testserver", follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def async_client(db_session: Session) -> AsyncGenerator[AsyncClient, None]:
    """Asynchronous httpx client with ASGITransport for concurrent testing."""
    if not APP_AVAILABLE or app is None:
        pytest.skip("FastAPI application app.main not yet available")

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


# =====================================================================
# Factory Helpers & Verification Utilities
# =====================================================================


def compute_client_hash(ip: str, user_agent: str, salt: str = "badge_salt_2026") -> str:
    """Compute SHA-256 client audit hash matching platform specification."""
    raw = f"{ip}:{user_agent}:{salt}".encode()
    return hashlib.sha256(raw).hexdigest()


def assert_valid_svg_xml(svg_text: str) -> ET.Element:
    """Assert that the given string is valid SVG XML and return root element."""
    assert svg_text is not None, "SVG content cannot be None"
    assert len(svg_text.strip()) > 0, "SVG content cannot be empty"
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        pytest.fail(f"SVG failed XML parsing: {exc}\nContent: {svg_text[:500]}")
    # Root tag should be svg or {http://www.w3.org/2000/svg}svg
    assert "svg" in root.tag.lower(), f"Root XML tag is not <svg>: {root.tag}"
    return root


def create_test_repository(
    session: Session,
    owner: str = "pallets",
    name: str = "flask",
    stars: int = 68000,
    primary_language: str | None = "Python",
    ci_status: str | None = "passing",
    claimed: bool = False,
    maintainer_handle: str | None = None,
    payout_address: str | None = None,
) -> Any:
    """Factory to insert a test Repository into database."""
    if Repository is None:
        pytest.skip("Repository model not yet available")
    repo = Repository(
        owner=owner,
        name=name,
        stars=stars,
        primary_language=primary_language,
        ci_status=ci_status,
        claimed=claimed,
        maintainer_handle=maintainer_handle,
        payout_address=payout_address,
    )
    session.add(repo)
    session.commit()
    session.refresh(repo)
    return repo


def create_test_ad(
    session: Session,
    sponsor_name: str = "Sentry",
    headline: str = "Catch Errors in Realtime",
    cta_text: str = "Sign Up Free",
    click_url: str = "https://sentry.io/welcome",
    target_language: str | None = "Python",
    cost_per_click: Decimal = Decimal("0.50"),
    remaining_budget: Decimal = Decimal("100.00"),
    total_budget: Decimal | None = Decimal("100.00"),
    active: bool = True,
) -> Any:
    """Factory to insert a test Ad campaign into database."""
    if Ad is None:
        pytest.skip("Ad model not yet available")
    # Support both cost_per_click and cpc attribute names
    init_kwargs = {
        "sponsor_name": sponsor_name,
        "headline": headline,
        "cta_text": cta_text,
        "click_url": click_url,
        "target_language": target_language,
        "remaining_budget": remaining_budget,
        "active": active,
    }
    if hasattr(Ad, "cost_per_click"):
        init_kwargs["cost_per_click"] = cost_per_click
    elif hasattr(Ad, "cpc"):
        init_kwargs["cpc"] = cost_per_click
    else:
        init_kwargs["cost_per_click"] = cost_per_click

    if hasattr(Ad, "total_budget") and total_budget is not None:
        init_kwargs["total_budget"] = total_budget

    ad = Ad(**init_kwargs)
    session.add(ad)
    session.commit()
    session.refresh(ad)
    return ad


def create_test_impression(
    session: Session,
    repo_id: int,
    ad_id: int | None = None,
    client_hash: str | None = None,
    timestamp: datetime | None = None,
) -> Any:
    """Factory to insert a test Impression into database."""
    if Impression is None:
        pytest.skip("Impression model not yet available")
    if client_hash is None:
        client_hash = compute_client_hash("127.0.0.1", "pytest-agent")
    if timestamp is None:
        timestamp = datetime.now(UTC)

    init_kwargs = {
        "repo_id": repo_id,
        "ad_id": ad_id,
        "client_hash": client_hash,
    }
    if hasattr(Impression, "created_at"):
        init_kwargs["created_at"] = timestamp
    elif hasattr(Impression, "timestamp"):
        init_kwargs["timestamp"] = timestamp

    imp = Impression(**init_kwargs)
    session.add(imp)
    session.commit()
    session.refresh(imp)
    return imp


def create_test_click(
    session: Session,
    repo_id: int,
    ad_id: int,
    client_hash: str | None = None,
    cost: Decimal = Decimal("0.50"),
    timestamp: datetime | None = None,
) -> Any:
    """Factory to insert a test Click into database."""
    if Click is None:
        pytest.skip("Click model not yet available")
    if client_hash is None:
        client_hash = compute_client_hash("127.0.0.1", "pytest-agent")
    if timestamp is None:
        timestamp = datetime.now(UTC)

    init_kwargs = {
        "repo_id": repo_id,
        "ad_id": ad_id,
        "client_hash": client_hash,
    }
    if hasattr(Click, "cost"):
        init_kwargs["cost"] = cost
    elif hasattr(Click, "cost_per_click"):
        init_kwargs["cost_per_click"] = cost

    if hasattr(Click, "created_at"):
        init_kwargs["created_at"] = timestamp
    elif hasattr(Click, "timestamp"):
        init_kwargs["timestamp"] = timestamp

    click = Click(**init_kwargs)
    session.add(click)
    session.commit()
    session.refresh(click)
    return click
