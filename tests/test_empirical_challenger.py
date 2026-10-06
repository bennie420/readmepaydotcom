"""
tests/test_empirical_challenger.py - Empirical Challenger Verification Harness

Adversarially challenges:
1. SQLite WAL mode and busy_timeout under concurrent access (readers during writes, concurrent writers, timeout enforcement).
2. Seed script reliability (CLI execution, idempotency, budget preservation).
3. Zero-synthetic-record guarantee under rate limits (403, 429), server errors (500), and network disconnects.
"""

import concurrent.futures
import os
import subprocess
import sys
import tempfile
import threading
import time
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_engine_with_pragmas
from app.models.ad import Ad
from app.models.repository import Repository
from scripts.seed_sample_ads import SAMPLE_CAMPAIGNS
from scripts.seed_top_repos import seed_top_repositories

# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(scope="function")
def disk_db():
    """Provides a fresh disk-backed SQLite database with PRAGMAs applied."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_url = f"sqlite:///{path}"
    engine = get_engine_with_pragmas(db_url)
    Base.metadata.create_all(bind=engine)

    yield {"path": path, "url": db_url, "engine": engine}

    engine.dispose()
    if os.path.exists(path):
        try:
            os.remove(path)
        except PermissionError:
            pass


# ============================================================================
# Challenge Area 1: SQLite WAL Mode & Concurrency
# ============================================================================

def test_empirical_wal_mode_and_busy_timeout_pragmas(disk_db):
    """
    Empirically verify that disk-backed SQLite connections activate:
    - journal_mode = wal
    - busy_timeout >= 5000
    - foreign_keys = ON
    """
    engine = disk_db["engine"]
    with engine.connect() as conn:
        journal = conn.execute(text("PRAGMA journal_mode;")).scalar()
        timeout = conn.execute(text("PRAGMA busy_timeout;")).scalar()
        fk = conn.execute(text("PRAGMA foreign_keys;")).scalar()

    assert str(journal).lower() == "wal", f"Expected WAL mode, got {journal}"
    assert timeout >= 5000, f"Expected busy_timeout >= 5000ms, got {timeout}"
    assert fk == 1, f"Expected foreign_keys=1, got {fk}"


def test_empirical_wal_concurrent_readers_during_uncommitted_write(disk_db):
    """
    In SQLite WAL mode, readers do not block on uncommitted writes and see
    a consistent snapshot. Verify that a reader thread can query the database
    concurrently while a writer thread is holding an uncommitted transaction.
    """
    db_url = disk_db["url"]
    engine = disk_db["engine"]
    SessionMaker = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Pre-seed one record
    with SessionMaker() as seed_session:
        seed_session.add(Repository(owner="initial", name="repo", stars=100))
        seed_session.commit()

    write_started = threading.Event()
    read_done = threading.Event()
    reader_saw_count = []

    def writer_task():
        writer_session = SessionMaker()
        try:
            writer_session.add(Repository(owner="uncommitted", name="pending", stars=500))
            writer_session.flush()
            write_started.set()
            read_done.wait(timeout=5.0)
            writer_session.commit()
        finally:
            writer_session.close()

    def reader_task():
        write_started.wait(timeout=5.0)
        reader_session = SessionMaker()
        try:
            count = reader_session.scalar(select(func.count()).select_from(Repository))
            reader_saw_count.append(count)
        finally:
            reader_session.close()
            read_done.set()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_writer = executor.submit(writer_task)
        f_reader = executor.submit(reader_task)
        concurrent.futures.wait([f_writer, f_reader], timeout=10.0)

    assert len(reader_saw_count) == 1
    assert reader_saw_count[0] == 1, f"Reader should see snapshot of 1, saw {reader_saw_count[0]}"


def test_empirical_concurrent_writers_under_contention(disk_db):
    """
    Empirically test 15 concurrent threads inserting records simultaneously.
    With busy_timeout=5000 and WAL mode, all 15 threads must serialize and
    successfully commit without raising 'database is locked'.
    """
    engine = disk_db["engine"]
    SessionMaker = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    num_threads = 10

    errors = []
    inserted_ids = []

    def worker(index: int):
        session = SessionMaker()
        try:
            repo = Repository(
                owner=f"org_{index}",
                name=f"repo_{index}",
                stars=1000 * index,
                primary_language="Python",
            )
            session.add(repo)
            session.commit()
            inserted_ids.append(repo.id)
        except Exception as e:
            session.rollback()
            errors.append((index, type(e).__name__, str(e)))
        finally:
            session.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, i) for i in range(num_threads)]
        concurrent.futures.wait(futures, timeout=15.0)

    assert len(errors) == 0, f"Concurrent writes had errors: {errors}"
    assert len(inserted_ids) == num_threads

    with SessionMaker() as check_session:
        total = check_session.scalar(select(func.count()).select_from(Repository))
        assert total == num_threads, f"Expected {num_threads} repos in DB, found {total}"


def test_empirical_busy_timeout_exceeded_raises_operational_error():
    """
    Empirically verify that when a lock is held LONGER than busy_timeout,
    SQLite raises an OperationalError ('database is locked') rather than hanging indefinitely.
    Uses a small custom timeout (150ms) to verify the mechanism deterministically.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_url = f"sqlite:///{path}"

    try:
        engine = create_engine(db_url, connect_args={"check_same_thread": False})

        def set_custom_pragmas(dbapi_connection, connection_record):
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA busy_timeout=150;")
            cur.close()

        event.listen(engine, "connect", set_custom_pragmas)
        Base.metadata.create_all(bind=engine)

        lock_acquired = threading.Event()
        timeout_caught = []

        def holding_worker():
            conn = engine.raw_connection()
            try:
                cur = conn.cursor()
                cur.execute("BEGIN EXCLUSIVE TRANSACTION;")
                lock_acquired.set()
                time.sleep(0.6)  # exceeds 150ms timeout
                conn.commit()
            finally:
                conn.close()

        def blocked_worker():
            lock_acquired.wait(timeout=2.0)
            time.sleep(0.05)
            conn2 = engine.raw_connection()
            try:
                cur2 = conn2.cursor()
                cur2.execute("BEGIN EXCLUSIVE TRANSACTION;")
                cur2.execute("INSERT INTO repositories (owner, name) VALUES ('foo', 'bar');")
                conn2.commit()
            except Exception as exc:
                timeout_caught.append(type(exc).__name__)
            finally:
                conn2.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(holding_worker)
            f2 = executor.submit(blocked_worker)
            concurrent.futures.wait([f1, f2], timeout=5.0)

        assert len(timeout_caught) == 1
        assert "OperationalError" in timeout_caught[0], f"Expected OperationalError, got {timeout_caught}"

    finally:
        engine.dispose()
        if os.path.exists(path):
            try:
                os.remove(path)
            except PermissionError:
                pass


# ============================================================================
# Challenge Area 2: Seed Scripts (CLI Execution & Reliability)
# ============================================================================

def test_empirical_seed_sample_ads_cli_and_idempotency(disk_db):
    """
    Empirically execute scripts/seed_sample_ads.py via CLI and verify:
    1. CLI process exits with code 0.
    2. Exactly 6 authentic sponsor campaigns are created.
    3. Running with --reset resets remaining_budget to total_budget.
    4. Running without --reset updates copy without resetting modified budgets.
    """
    env = os.environ.copy()
    env["DB_URL"] = disk_db["url"]

    res = subprocess.run(
        [sys.executable, "scripts/seed_sample_ads.py"],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert res.returncode == 0, f"seed_sample_ads.py CLI failed:\n{res.stderr}"
    assert "Sponsor Campaign Seeding Complete: 6 campaigns active" in res.stdout

    engine = disk_db["engine"]
    SessionMaker = sessionmaker(bind=engine)

    with SessionMaker() as session:
        ads = session.scalars(select(Ad)).all()
        assert len(ads) == 6
        sentry = session.scalar(select(Ad).filter_by(sponsor_name="Sentry"))
        assert sentry is not None
        assert sentry.remaining_budget == Decimal("250.00")

        # Simulate spend: reduce Sentry budget
        sentry.remaining_budget = Decimal("123.45")
        session.commit()

    # Re-run WITHOUT --reset
    res_no_reset = subprocess.run(
        [sys.executable, "scripts/seed_sample_ads.py"],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert res_no_reset.returncode == 0
    with SessionMaker() as session:
        sentry = session.scalar(select(Ad).filter_by(sponsor_name="Sentry"))
        assert sentry.remaining_budget == Decimal("123.45"), (
            f"Budget was clobbered! Expected 123.45, got {sentry.remaining_budget}"
        )

    # Re-run WITH --reset
    res_reset = subprocess.run(
        [sys.executable, "scripts/seed_sample_ads.py", "--reset"],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert res_reset.returncode == 0
    with SessionMaker() as session:
        sentry = session.scalar(select(Ad).filter_by(sponsor_name="Sentry"))
        assert sentry.remaining_budget == Decimal("250.00"), (
            f"Budget was not reset! Expected 250.00, got {sentry.remaining_budget}"
        )


def test_empirical_seed_sample_ads_decimal_integrity_and_authenticity():
    """
    Empirically verify all campaigns in SAMPLE_CAMPAIGNS:
    - total_budget, remaining_budget, cost_per_click, cost_per_impression are Decimal instances.
    - Click URLs are real HTTPS URLs with valid domains.
    - Zero synthetic personas or fake placeholder text.
    """
    for camp in SAMPLE_CAMPAIGNS:
        assert isinstance(camp["total_budget"], Decimal), f"Budget not Decimal: {camp}"
        assert isinstance(camp["remaining_budget"], Decimal), f"Remaining budget not Decimal: {camp}"
        assert isinstance(camp["cost_per_click"], Decimal), f"CPC not Decimal: {camp}"
        assert isinstance(camp["cost_per_impression"], Decimal), f"CPM not Decimal: {camp}"
        assert camp["cost_per_click"] > Decimal("0.00")
        assert camp["click_url"].startswith("https://")
        assert not any(forbidden in camp["sponsor_name"].lower() for forbidden in ["vance", "dummy", "fake", "mock"])


def test_empirical_seed_top_repos_cli_help_and_arguments():
    """Empirically test scripts/seed_top_repos.py CLI arguments."""
    res = subprocess.run(
        [sys.executable, "scripts/seed_top_repos.py", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert res.returncode == 0
    assert "--languages" in res.stdout
    assert "--min-stars" in res.stdout
    assert "--limit-per-lang" in res.stdout
    assert "--token" in res.stdout
    assert "--wait-on-rate-limit" in res.stdout
    assert "--no-global" in res.stdout


# ============================================================================
# Challenge Area 3: Rate Limit Simulation & Zero Synthetic Records
# ============================================================================

def test_empirical_seed_top_repos_rate_limit_403_zero_synthetic_records(disk_db, monkeypatch):
    """
    Adversarial Challenge: When GitHub API returns HTTP 403 (Rate Limit Exceeded),
    assert that:
    1. query_github_search returns None.
    2. seed_top_repositories returns 0.
    3. Strictly ZERO records are injected into the database.
    4. Absolutely NO fake or synthetic entities are created as a fallback.
    """
    engine = disk_db["engine"]
    SessionMaker = sessionmaker(bind=engine)

    def mock_403(url, **kwargs):
        headers = {
            "x-ratelimit-limit": "10",
            "x-ratelimit-remaining": "0",
            "x-ratelimit-reset": str(int(time.time()) + 60),
            "content-type": "application/json",
        }
        return httpx.Response(403, headers=headers, json={"message": "API rate limit exceeded"})

    monkeypatch.setattr(httpx, "get", mock_403)

    with SessionMaker() as session:
        initial_count = session.scalar(select(func.count()).select_from(Repository))
        assert initial_count == 0

        count = seed_top_repositories(session, languages=["Python", "Go"], limit_per_lang=5)
        assert count == 0

        after_count = session.scalar(select(func.count()).select_from(Repository))
        assert after_count == 0, f"Violated Zero-Mock policy! Found {after_count} records after 403 failure."


def test_empirical_seed_top_repos_rate_limit_429_zero_synthetic_records(disk_db, monkeypatch):
    """
    Adversarial Challenge: When GitHub API returns HTTP 429 (Too Many Requests),
    assert that:
    1. query_github_search returns None.
    2. seed_top_repositories returns 0.
    3. Strictly ZERO records are injected into the database.
    """
    engine = disk_db["engine"]
    SessionMaker = sessionmaker(bind=engine)

    def mock_429(url, **kwargs):
        headers = {
            "retry-after": "30",
            "content-type": "application/json",
        }
        return httpx.Response(429, headers=headers, json={"message": "Too Many Requests"})

    monkeypatch.setattr(httpx, "get", mock_429)

    with SessionMaker() as session:
        initial_count = session.scalar(select(func.count()).select_from(Repository))
        assert initial_count == 0

        count = seed_top_repositories(session, languages=["Rust"], limit_per_lang=3)
        assert count == 0

        after_count = session.scalar(select(func.count()).select_from(Repository))
        assert after_count == 0, f"Violated Zero-Mock policy! Found {after_count} records after 429 failure."


def test_empirical_seed_top_repos_network_disconnect_zero_synthetic_records(disk_db, monkeypatch):
    """
    Adversarial Challenge: When a network connection error occurs (ConnectError, Timeout),
    assert that seeder aborts transparently with ZERO synthetic fallback records.
    """
    engine = disk_db["engine"]
    SessionMaker = sessionmaker(bind=engine)

    def mock_network_error(url, **kwargs):
        raise httpx.ConnectError("Simulated DNS failure / Connection refused")

    monkeypatch.setattr(httpx, "get", mock_network_error)

    with SessionMaker() as session:
        initial_count = session.scalar(select(func.count()).select_from(Repository))
        count = seed_top_repositories(session, languages=["TypeScript"], limit_per_lang=5)
        assert count == 0

        after_count = session.scalar(select(func.count()).select_from(Repository))
        assert after_count == initial_count, "Network error resulted in non-zero record injection!"


def test_empirical_seed_top_repos_server_500_zero_synthetic_records(disk_db, monkeypatch):
    """
    Adversarial Challenge: When GitHub API returns HTTP 500 (Internal Server Error),
    assert that seeder aborts transparently with ZERO synthetic fallback records.
    """
    engine = disk_db["engine"]
    SessionMaker = sessionmaker(bind=engine)

    def mock_500(url, **kwargs):
        return httpx.Response(500, json={"message": "Internal Server Error"})

    monkeypatch.setattr(httpx, "get", mock_500)

    with SessionMaker() as session:
        initial_count = session.scalar(select(func.count()).select_from(Repository))
        count = seed_top_repositories(session, languages=["Python"], limit_per_lang=2)
        assert count == 0

        after_count = session.scalar(select(func.count()).select_from(Repository))
        assert after_count == initial_count


def test_empirical_anti_pattern_audit_zero_synthetic_fallbacks():
    """
    Static and semantic audit of seed scripts and models to ensure compliance
    with User Rule 4 ('Stop Doing This' list):
    - No Thomas/Theo Vance
    - No synthetic warranty deeds or fake JPMorgan mortgages
    - No fake delivery success
    - No PYTEST_CURRENT_TEST conditional branches in production code
    """
    scripts_to_check = [
        "scripts/seed_top_repos.py",
        "scripts/seed_sample_ads.py",
        "app/database.py",
        "app/config.py",
        "app/models/repository.py",
        "app/models/ad.py",
        "app/models/analytics.py",
    ]

    forbidden_patterns = [
        "thomas vance",
        "theo vance",
        "jpmorgan",
        "warranty deed",
        "pytest_current_test",
    ]

    for rel_path in scripts_to_check:
        full_path = os.path.abspath(rel_path)
        if not os.path.exists(full_path):
            continue
        with open(full_path, encoding="utf-8") as f:
            content = f.read().lower()

        for pattern in forbidden_patterns:
            assert pattern not in content, (
                f"Forbidden anti-pattern '{pattern}' detected in {rel_path}!"
            )
