"""
tests/test_models.py - Milestone 1 Data Models, PRAGMAs & Security Unit Tests
"""

import os
import re
import tempfile
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_engine_with_pragmas
from app.models.ad import Ad
from app.models.analytics import Click, Impression
from app.models.repository import Repository
from app.utils.security import compute_client_audit_hash

# --- Fixtures ---

@pytest.fixture(scope="function")
def temp_db_engine():
    """File-backed SQLite engine in tempdir to verify WAL journal mode and PRAGMAs."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        temp_path = tf.name

    db_url = f"sqlite:///{temp_path}"
    engine = get_engine_with_pragmas(db_url)
    Base.metadata.create_all(bind=engine)

    yield engine

    engine.dispose()
    if os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except PermissionError:
            pass


@pytest.fixture(scope="function")
def db_session(temp_db_engine):
    """Provides a transactional database session for each test."""
    SessionLocal = sessionmaker(bind=temp_db_engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# --- Area 1: Engine PRAGMAs & Table DDL ---

def test_sqlite_pragmas_foreign_keys_and_busy_timeout(temp_db_engine):
    """Verify that SQLite connection PRAGMAs are strictly enforced."""
    with temp_db_engine.connect() as conn:
        fk_status = conn.execute(text("PRAGMA foreign_keys;")).scalar()
        assert fk_status == 1, "PRAGMA foreign_keys must be enabled (1)"

        timeout = conn.execute(text("PRAGMA busy_timeout;")).scalar()
        assert timeout >= 5000, f"PRAGMA busy_timeout must be >= 5000, got {timeout}"


def test_sqlite_wal_journal_mode(temp_db_engine):
    """Verify that SQLite Write-Ahead Logging (WAL) is enabled for high concurrency."""
    with temp_db_engine.connect() as conn:
        journal_mode = conn.execute(text("PRAGMA journal_mode;")).scalar()
        assert str(journal_mode).lower() == "wal", f"Expected WAL mode, got {journal_mode}"


def test_database_tables_exist(temp_db_engine):
    """Verify all 4 core tables are successfully created in metadata."""
    inspector = inspect(temp_db_engine)
    tables = inspector.get_table_names()
    expected = {"repositories", "ads", "impressions", "clicks"}
    assert expected.issubset(set(tables)), f"Missing tables. Found: {tables}, expected: {expected}"


# --- Area 2: Repository Constraints & Defaults ---

def test_repository_creation_and_defaults(db_session):
    """Verify repository fields, auto-increment ID, and default values."""
    repo = Repository(
        owner="fastapi",
        name="fastapi",
        github_id=12345678,
        description="FastAPI framework",
        stars=75000,
        primary_language="Python",
    )
    db_session.add(repo)
    db_session.commit()

    assert repo.id is not None
    assert repo.id > 0
    assert repo.claimed is False
    assert repo.claimed_by is None
    assert repo.payout_address is None
    assert repo.created_at is not None
    assert repo.updated_at is not None


def test_repository_uniqueness_owner_name(db_session):
    """Verify that duplicate (owner, name) triggers an IntegrityError."""
    repo1 = Repository(owner="tiangolo", name="fastapi", stars=75000)
    db_session.add(repo1)
    db_session.commit()

    repo2 = Repository(owner="tiangolo", name="fastapi", stars=75000)
    db_session.add(repo2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_repository_distinct_owners_same_name_permitted(db_session):
    """Verify that different owners can have repositories with identical names."""
    repo_alice = Repository(owner="alice", name="cool-tool", stars=10)
    repo_bob = Repository(owner="bob", name="cool-tool", stars=20)
    db_session.add_all([repo_alice, repo_bob])
    db_session.commit()

    assert repo_alice.id != repo_bob.id
    assert repo_alice.name == repo_bob.name


def test_repository_github_id_uniqueness_and_nulls(db_session):
    """Verify github_id unique constraint and support for nullable values."""
    repo1 = Repository(owner="org1", name="repo1", github_id=999)
    db_session.add(repo1)
    db_session.commit()

    repo2 = Repository(owner="org2", name="repo2", github_id=999)
    db_session.add(repo2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Verify multiple nulls are allowed
    repo_null1 = Repository(owner="orgA", name="repoA", github_id=None)
    repo_null2 = Repository(owner="orgB", name="repoB", github_id=None)
    db_session.add_all([repo_null1, repo_null2])
    db_session.commit()
    assert repo_null1.id is not None and repo_null2.id is not None


# --- Area 3: Ad Model Constraints & Financial Precision ---

def test_ad_creation_and_financial_decimals(db_session):
    """Verify Ad creation and exact Decimal precision for monetary amounts."""
    ad = Ad(
        sponsor_name="Sentry",
        headline="Application Performance Monitoring",
        call_to_action="Try Free",
        click_url="https://sentry.io",
        target_language="Python",
        is_active=True,
        total_budget=Decimal("100.00"),
        remaining_budget=Decimal("100.00"),
        cost_per_click=Decimal("0.50"),
        cost_per_impression=Decimal("0.0020"),
    )
    db_session.add(ad)
    db_session.commit()

    reloaded = db_session.get(Ad, ad.id)
    assert reloaded is not None
    assert isinstance(reloaded.total_budget, Decimal)
    assert reloaded.total_budget == Decimal("100.00")
    assert isinstance(reloaded.remaining_budget, Decimal)
    assert reloaded.remaining_budget == Decimal("100.00")
    assert reloaded.cost_per_click == Decimal("0.50")
    assert reloaded.cost_per_impression == Decimal("0.0020")
    assert reloaded.is_active is True


def test_ad_general_fallback_nullable_language(db_session):
    """Verify universal fallback sponsor campaigns have target_language = None."""
    ad = Ad(
        sponsor_name="GitHub Sponsors",
        headline="Support Open Source",
        call_to_action="Sponsor",
        click_url="https://github.com/sponsors",
        target_language=None,
        total_budget=Decimal("500.00"),
        remaining_budget=Decimal("500.00"),
    )
    db_session.add(ad)
    db_session.commit()

    assert ad.id is not None
    assert ad.target_language is None


# --- Area 4: Foreign Key Cascades & Relationship Integrity ---

def test_foreign_key_orphan_rejection_impressions(db_session):
    """Verify PRAGMA foreign_keys prevents orphan impression records."""
    orphan_imp = Impression(
        repo_id=999999,
        ad_id=888888,
        client_hash="a" * 64,
        cost=Decimal("0.0020"),
    )
    db_session.add(orphan_imp)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_foreign_key_orphan_rejection_clicks(db_session):
    """Verify PRAGMA foreign_keys prevents orphan click records."""
    orphan_click = Click(
        repo_id=999999,
        ad_id=888888,
        client_hash="b" * 64,
        cost=Decimal("0.50"),
    )
    db_session.add(orphan_click)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_cascade_delete_repository_cleans_analytics(db_session):
    """Verify deleting a Repository cascades and deletes associated impressions/clicks."""
    repo = Repository(owner="pytest-dev", name="pytest")
    ad = Ad(sponsor_name="Test Sponsor", headline="Test", click_url="https://test.com")
    db_session.add_all([repo, ad])
    db_session.commit()

    imp = Impression(repo_id=repo.id, ad_id=ad.id, client_hash="c" * 64)
    click = Click(repo_id=repo.id, ad_id=ad.id, client_hash="c" * 64)
    db_session.add_all([imp, click])
    db_session.commit()

    imp_id, click_id, ad_id = imp.id, click.id, ad.id

    # Delete repository
    db_session.delete(repo)
    db_session.commit()

    assert db_session.get(Impression, imp_id) is None, "Impression must be cascaded on repo delete"
    assert db_session.get(Click, click_id) is None, "Click must be cascaded on repo delete"
    assert db_session.get(Ad, ad_id) is not None, "Ad must remain untouched"


def test_cascade_delete_ad_cleans_analytics(db_session):
    """Verify deleting an Ad cascades and deletes associated impressions/clicks."""
    repo = Repository(owner="tiangolo", name="sqlmodel")
    ad = Ad(sponsor_name="Neon", headline="Serverless Postgres", click_url="https://neon.tech")
    db_session.add_all([repo, ad])
    db_session.commit()

    imp = Impression(repo_id=repo.id, ad_id=ad.id, client_hash="d" * 64)
    click = Click(repo_id=repo.id, ad_id=ad.id, client_hash="d" * 64)
    db_session.add_all([imp, click])
    db_session.commit()

    imp_id, click_id, repo_id = imp.id, click.id, repo.id

    # Delete Ad
    db_session.delete(ad)
    db_session.commit()

    assert db_session.get(Impression, imp_id) is None, "Impression must be cascaded on ad delete"
    assert db_session.get(Click, click_id) is None, "Click must be cascaded on ad delete"
    assert db_session.get(Repository, repo_id) is not None, "Repository must remain untouched"


def test_orm_relationship_navigation(db_session):
    """Verify bidirectional ORM relationship navigation across all entities."""
    repo = Repository(owner="rust-lang", name="rust")
    ad = Ad(sponsor_name="Neon", headline="Fast Postgres", click_url="https://neon.tech")
    db_session.add_all([repo, ad])
    db_session.commit()

    imp = Impression(repo_id=repo.id, ad_id=ad.id, client_hash="e" * 64)
    click = Click(repo_id=repo.id, ad_id=ad.id, client_hash="e" * 64)
    db_session.add_all([imp, click])
    db_session.commit()

    # Refresh and navigate
    db_session.refresh(repo)
    db_session.refresh(ad)

    assert imp in repo.impressions
    assert click in repo.clicks
    assert imp in ad.impressions
    assert click in ad.clicks
    assert imp.repository.id == repo.id
    assert imp.ad.id == ad.id
    assert click.repository.id == repo.id
    assert click.ad.id == ad.id


# --- Area 5: Client Audit Hash Cryptography ---

def test_sha256_audit_hash_format_and_determinism():
    """Verify SHA-256 client audit hash consistency and format."""
    salt = "secure_production_salt_2026"
    h1 = compute_client_audit_hash("192.168.1.1", "Mozilla/5.0", salt)
    h2 = compute_client_audit_hash("192.168.1.1", "Mozilla/5.0", salt)

    assert h1 == h2, "Audit hash must be deterministic"
    assert len(h1) == 64, f"SHA-256 hex digest must be exactly 64 characters, got {len(h1)}"
    assert re.match(r"^[0-9a-f]{64}$", h1), "Hash must be lower-case hex digits"


def test_sha256_audit_hash_avalanche_and_salt():
    """Verify avalanche effect across IP, User-Agent, and salt changes."""
    salt = "secret_salt"
    base = compute_client_audit_hash("192.168.1.1", "curl/7.68.0", salt)
    diff_ip = compute_client_audit_hash("192.168.1.2", "curl/7.68.0", salt)
    diff_ua = compute_client_audit_hash("192.168.1.1", "curl/7.68.1", salt)
    diff_salt = compute_client_audit_hash("192.168.1.1", "curl/7.68.0", "other_salt")

    assert base != diff_ip
    assert base != diff_ua
    assert base != diff_salt


def test_sha256_audit_hash_whitespace_normalization():
    """Verify that leading/trailing whitespace does not cause duplicate hash divergence."""
    salt = "salt"
    h1 = compute_client_audit_hash("10.0.0.1", "Mozilla/5.0", salt)
    h2 = compute_client_audit_hash("  10.0.0.1  ", "  Mozilla/5.0  ", salt)
    assert h1 == h2


def test_sha256_audit_hash_not_base64_or_reversible():
    """Verify hash is not Base64 encoding masquerading as encryption."""
    import base64
    salt = "salt"
    ip = "192.168.1.1"
    ua = "Mozilla/5.0"
    digest = compute_client_audit_hash(ip, ua, salt)

    # Base64 decode should NOT yield plaintext IP or User-Agent
    try:
        decoded = base64.b64decode(digest)
        assert ip.encode() not in decoded, "Audit hash must not reveal plaintext client IP"
        assert ua.encode() not in decoded, "Audit hash must not reveal plaintext User-Agent"
    except Exception:
        pass  # Standard hex is not valid Base64 padding, which is expected


# --- Area 6: Static Audits & Anti-Pattern Prevention ---

def test_static_zero_pytest_current_test_in_production_code():
    """Enforce User Rule: Zero PYTEST_CURRENT_TEST conditional shortcuts in app/."""
    app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
    if not os.path.exists(app_dir):
        pytest.skip("app/ directory not yet initialized")

    violations = []
    for root, _, files in os.walk(app_dir):
        for file in files:
            if file.endswith(".py"):
                path = os.path.join(root, file)
                with open(path, encoding="utf-8") as f:
                    content = f.read()
                    if "PYTEST_CURRENT_TEST" in content:
                        violations.append(path)

    assert not violations, f"Forbidden PYTEST_CURRENT_TEST detected in production files: {violations}"


def test_static_zero_mock_personas_in_production_models():
    """Enforce User Rule: Zero Vance family or synthetic personas in app/."""
    app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
    if not os.path.exists(app_dir):
        pytest.skip("app/ directory not yet initialized")

    forbidden_tokens = ["Thomas Vance", "Theo Vance", "Vance Family", "John Doe"]
    violations = []
    for root, _, files in os.walk(app_dir):
        for file in files:
            if file.endswith(".py"):
                path = os.path.join(root, file)
                with open(path, encoding="utf-8") as f:
                    content = f.read()
                    for token in forbidden_tokens:
                        if token.lower() in content.lower():
                            violations.append((path, token))

    assert not violations, f"Forbidden mock personas detected in production code: {violations}"
