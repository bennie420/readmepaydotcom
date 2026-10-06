"""
tests/test_challenger_m3_empirical.py - Empirical Adversarial Challenge Suite for Milestone 3.

Empirical verification of:
1. Exact Decimal precision in budget deductions (fractional cents, zero float drift, reload stability).
2. Ad auto-deactivation boundaries (remaining_budget < cpc, exact 0.00 balance).
3. Negative budget prevention under rapid and multi-threaded concurrent requests (WAL mode).
4. 1-hour sliding-window impression deduplication and boundary transitions.
5. GitHub Camo proxy IP extraction (X-Forwarded-For leftmost IP vs Camo UA detection).
6. Multi-entity deduplication isolation (repo_id, ad_id, client_hash).
"""

import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request
from starlette.testclient import TestClient

from app.database import Base, get_engine_with_pragmas
from app.models.ad import Ad
from app.models.analytics import Click, Impression
from app.models.repository import Repository
from app.services.tracking_service import (
    extract_client_ip,
    is_camo_request,
    record_click,
    record_impression,
)
from tests.conftest import create_test_ad, create_test_repository

# =====================================================================
# Fixtures for File-Backed SQLite WAL Database
# =====================================================================

@pytest.fixture(scope="function")
def file_db_env():
    """Provides a temporary file-backed SQLite database enforcing WAL and foreign keys."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        temp_path = tf.name

    db_url = f"sqlite:///{temp_path}"
    engine = get_engine_with_pragmas(db_url)
    Base.metadata.create_all(bind=engine)
    SessionMaker = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    yield engine, SessionMaker

    engine.dispose()
    if os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except PermissionError:
            pass


# =====================================================================
# 1. Exact Decimal Precision & Fractional Cent Deductions
# =====================================================================

def test_exact_decimal_fractional_cpc_depletion(client: TestClient, db_session):
    """
    Empirical challenge: 40 sequential clicks of fractional CPC $0.025 on $1.000 budget.
    Must deduct with exact Decimal math, hitting exactly $0.00 without floating-point drift,
    auto-deactivating on the 40th click and returning 404 on the 41st click.
    """
    repo = create_test_repository(db_session)
    ad = create_test_ad(
        db_session,
        remaining_budget=Decimal("1.000"),
        total_budget=Decimal("1.000"),
        cost_per_click=Decimal("0.025"),
        active=True,
    )

    expected_remaining = Decimal("1.000")
    cpc = Decimal("0.025")

    for i in range(1, 41):
        res = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
        assert res.status_code == 302, f"Click {i} should return 302"
        expected_remaining -= cpc
        db_session.refresh(ad)
        assert ad.remaining_budget == expected_remaining, f"Mismatch at click {i}"
        if i < 40:
            assert ad.is_active is True
        else:
            assert ad.is_active is False
            assert ad.remaining_budget == Decimal("0.00")

    # 41st click must fail (budget exhausted)
    res_41 = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert res_41.status_code == 404
    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("0.00")
    assert ad.is_active is False

    # Check database clicks integrity
    clicks = db_session.query(Click).filter_by(ad_id=ad.id).all()
    assert len(clicks) == 40
    total_charged = sum(c.cost for c in clicks)
    assert total_charged == Decimal("1.000")

    # Verify fractional 50/50 split on each click
    for c in clicks:
        assert c.cost == Decimal("0.025")
        assert c.maintainer_cut + c.platform_cut == Decimal("0.025")
        assert c.maintainer_cut == Decimal("0.0125")
        assert c.platform_cut == Decimal("0.0125")


def test_sub_cent_micro_cpc_precision(client: TestClient, db_session):
    """
    Empirical challenge: Micro-CPC $0.0005 over $0.0100 budget (20 clicks).
    Verifies 4-decimal precision holds without float drift.
    """
    repo = create_test_repository(db_session)
    ad = create_test_ad(
        db_session,
        remaining_budget=Decimal("0.0100"),
        cost_per_click=Decimal("0.0005"),
        active=True,
    )

    for i in range(20):
        res = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
        assert res.status_code == 302

    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("0.00")
    assert ad.is_active is False

    clicks = db_session.query(Click).filter_by(ad_id=ad.id).all()
    assert len(clicks) == 20
    assert sum(c.cost for c in clicks) == Decimal("0.0100")


def test_sqlite_wal_persistence_reload_decimal_precision(file_db_env):
    """
    Empirical challenge: Decimal precision survives database reload from disk
    without converting into IEEE 754 floating-point drift.
    """
    engine, SessionMaker = file_db_env
    session = SessionMaker()

    repo = Repository(owner="rust-lang", name="rust", stars=95000, primary_language="Rust")
    session.add(repo)
    ad = Ad(
        sponsor_name="RustCorp",
        headline="Compiler Speedups",
        click_url="https://rustcorp.example.com",
        remaining_budget=Decimal("9.975"),
        total_budget=Decimal("10.000"),
        cost_per_click=Decimal("0.025"),
        is_active=True,
    )
    session.add(ad)
    session.commit()
    ad_id = ad.id
    session.close()

    # Reopen fresh session
    new_session = SessionMaker()
    reloaded_ad = new_session.get(Ad, ad_id)
    assert reloaded_ad is not None
    assert isinstance(reloaded_ad.remaining_budget, Decimal)
    assert reloaded_ad.remaining_budget == Decimal("9.975")
    assert reloaded_ad.cost_per_click == Decimal("0.025")
    new_session.close()


# =====================================================================
# 2. Budget Depletion & Ad Deactivation Boundaries
# =====================================================================

def test_ad_deactivates_when_remaining_budget_less_than_cpc(client: TestClient, db_session):
    """
    Empirical challenge: Remaining budget is $0.75, CPC is $0.50.
    First click leaves $0.25. Since $0.25 < $0.50, ad must deactivate immediately.
    Second click must return 404 and NOT deduct the remaining $0.25.
    """
    repo = create_test_repository(db_session)
    ad = create_test_ad(
        db_session,
        remaining_budget=Decimal("0.75"),
        cost_per_click=Decimal("0.50"),
        active=True,
    )

    # First click succeeds
    r1 = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert r1.status_code == 302
    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("0.25")
    assert ad.is_active is False

    # Second click rejected
    r2 = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
    assert r2.status_code == 404
    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("0.25")  # Must NOT be modified
    assert ad.is_active is False

    clicks = db_session.query(Click).filter_by(ad_id=ad.id).all()
    assert len(clicks) == 1


def test_rapid_hammering_after_exhaustion_never_negative(client: TestClient, db_session):
    """
    Empirical challenge: Hammering an already exhausted ad with 50 rapid requests
    never drives the remaining budget below 0.00 and logs 0 additional clicks.
    """
    repo = create_test_repository(db_session)
    ad = create_test_ad(
        db_session,
        remaining_budget=Decimal("0.00"),
        cost_per_click=Decimal("1.00"),
        active=False,
    )

    for _ in range(50):
        res = client.get(f"/click/{ad.id}/{repo.id}", follow_redirects=False)
        assert res.status_code == 404

    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("0.00")
    assert ad.is_active is False

    clicks_count = db_session.query(Click).filter_by(ad_id=ad.id).count()
    assert clicks_count == 0


# =====================================================================
# 3. Concurrency & Race Condition Integrity in WAL Mode
# =====================================================================

def test_concurrent_clicks_wal_exact_deduction(file_db_env):
    """
    Empirical challenge: Multi-threaded concurrent clicks on an ad with exact budget
    for 2 clicks (remaining_budget = $2.00, cpc = $1.00).
    20 concurrent threads attempt to click simultaneously across distinct DB sessions.
    Must guarantee:
    1. remaining_budget NEVER drops below 0.00.
    2. Exactly 2 clicks are successfully recorded (no double spending).
    3. Exactly 2 threads succeed; remaining 18 receive 404.
    """
    engine, SessionMaker = file_db_env
    init_session = SessionMaker()

    repo = Repository(owner="pallets", name="flask", stars=65000, primary_language="Python")
    init_session.add(repo)
    ad = Ad(
        sponsor_name="FlaskSupporter",
        headline="Best Python Hosting",
        click_url="https://hosting.example.com",
        remaining_budget=Decimal("2.00"),
        total_budget=Decimal("2.00"),
        cost_per_click=Decimal("1.00"),
        is_active=True,
    )
    init_session.add(ad)
    init_session.commit()
    repo_id = repo.id
    ad_id = ad.id
    init_session.close()

    success_count = 0
    failure_count = 0

    def worker_click(thread_idx: int):
        s = SessionMaker()
        try:
            dest, click_rec = record_click(
                db=s,
                ad_id=ad_id,
                repo_id=repo_id,
                client_ip=f"192.168.1.{thread_idx}",
                user_agent=f"WorkerThread/{thread_idx}",
            )
            return True, dest
        except HTTPException as exc:
            return False, exc.status_code
        except Exception as e:
            return False, str(e)
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker_click, i) for i in range(20)]
        for f in as_completed(futures):
            ok, _ = f.result()
            if ok:
                success_count += 1
            else:
                failure_count += 1

    check_session = SessionMaker()
    final_ad = check_session.get(Ad, ad_id)
    recorded_clicks = check_session.query(Click).filter_by(ad_id=ad_id).all()

    # Crucial empirical assertions
    assert final_ad.remaining_budget >= Decimal("0.00"), "Budget became negative!"
    assert final_ad.remaining_budget == Decimal("0.00"), "Budget did not hit exact 0.00"
    assert final_ad.is_active is False
    assert len(recorded_clicks) == 2, f"Expected 2 recorded clicks, found {len(recorded_clicks)}"
    assert success_count == 2, f"Expected 2 successful clicks, got {success_count}"
    assert failure_count == 18, f"Expected 18 rejected clicks, got {failure_count}"
    check_session.close()


def test_concurrent_clicks_fractional_cpc_budget_accounting(file_db_env):
    """
    Empirical challenge: 30 concurrent threads hammering an ad with $0.25 CPC and $2.50 budget (10 clicks).
    Guarantees no race condition inflates clicks beyond budget capacity.
    """
    engine, SessionMaker = file_db_env
    init_session = SessionMaker()

    repo = Repository(owner="torvalds", name="linux", stars=170000, primary_language="C")
    init_session.add(repo)
    ad = Ad(
        sponsor_name="KernelSponsor",
        headline="Enterprise Linux Tools",
        click_url="https://linux.example.com",
        remaining_budget=Decimal("2.50"),
        total_budget=Decimal("2.50"),
        cost_per_click=Decimal("0.25"),
        is_active=True,
    )
    init_session.add(ad)
    init_session.commit()
    repo_id = repo.id
    ad_id = ad.id
    init_session.close()

    def worker_click(thread_idx: int):
        s = SessionMaker()
        try:
            record_click(
                db=s,
                ad_id=ad_id,
                repo_id=repo_id,
                client_ip=f"10.0.0.{thread_idx}",
                user_agent=f"Client/{thread_idx}",
            )
            return True
        except HTTPException:
            return False
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(worker_click, range(30)))

    successes = sum(1 for r in results if r)
    check_session = SessionMaker()
    final_ad = check_session.get(Ad, ad_id)
    clicks = check_session.query(Click).filter_by(ad_id=ad_id).all()

    assert final_ad.remaining_budget == Decimal("0.00")
    assert final_ad.is_active is False
    assert successes == 10
    assert len(clicks) == 10
    assert sum(c.cost for c in clicks) == Decimal("2.50")
    check_session.close()


# =====================================================================
# 4. 1-Hour Sliding-Window Impression Deduplication
# =====================================================================

def test_hourly_sliding_window_deduplication_exact_boundary(db_session):
    """
    Empirical challenge:
    1. First view creates impression row at t=0.
    2. Repeated views within 1-hour window return None (count stays 1).
    3. View after 1 hour (simulated by updating previous timestamp to 61 mins ago)
       successfully records a NEW impression row (count becomes 2).
    4. Repeated view within 1 hour of second impression returns None (count stays 2).
    """
    repo = create_test_repository(db_session)
    ad = create_test_ad(db_session)

    ip = "198.51.100.77"
    ua = "SlidingWindowBot/1.0"

    # View 1: Initial impression
    imp1 = record_impression(
        db=db_session,
        ad_id=ad.id,
        repo_id=repo.id,
        client_ip=ip,
        user_agent=ua,
    )
    assert imp1 is not None
    assert db_session.query(Impression).filter_by(repo_id=repo.id).count() == 1

    # Views within the 1-hour window: must deduplicate
    for _ in range(5):
        dup = record_impression(
            db=db_session,
            ad_id=ad.id,
            repo_id=repo.id,
            client_ip=ip,
            user_agent=ua,
        )
        assert dup is None
    assert db_session.query(Impression).filter_by(repo_id=repo.id).count() == 1

    # Simulate passage of time: set imp1 timestamp to 65 minutes ago
    imp1.timestamp = datetime.now(UTC) - timedelta(minutes=65)
    db_session.commit()

    # View 2: Now beyond 1 hour -> must record a second impression!
    imp2 = record_impression(
        db=db_session,
        ad_id=ad.id,
        repo_id=repo.id,
        client_ip=ip,
        user_agent=ua,
    )
    assert imp2 is not None
    assert imp2.id != imp1.id
    assert db_session.query(Impression).filter_by(repo_id=repo.id).count() == 2

    # View 3: Immediately after view 2 -> must deduplicate against view 2
    dup2 = record_impression(
        db=db_session,
        ad_id=ad.id,
        repo_id=repo.id,
        client_ip=ip,
        user_agent=ua,
    )
    assert dup2 is None
    assert db_session.query(Impression).filter_by(repo_id=repo.id).count() == 2


def test_sliding_window_multi_entity_isolation(db_session):
    """
    Empirical challenge: Deduplication is strictly scoped to (repo_id, ad_id, client_hash).
    Different client, different repo, or different ad must NOT be suppressed.
    """
    repo1 = create_test_repository(db_session, owner="owner1", name="repo1")
    repo2 = create_test_repository(db_session, owner="owner2", name="repo2")
    ad1 = create_test_ad(db_session, headline="Ad 1")
    ad2 = create_test_ad(db_session, headline="Ad 2")

    client_a_ip = "10.0.0.1"
    client_b_ip = "10.0.0.2"
    ua = "Mozilla/5.0"

    # Client A views (Repo 1, Ad 1)
    imp_a1 = record_impression(db_session, ad1.id, repo1.id, client_a_ip, ua)
    assert imp_a1 is not None

    # Client A views (Repo 1, Ad 1) again -> deduplicated
    assert record_impression(db_session, ad1.id, repo1.id, client_a_ip, ua) is None

    # Client B views (Repo 1, Ad 1) -> distinct client, allowed!
    imp_b1 = record_impression(db_session, ad1.id, repo1.id, client_b_ip, ua)
    assert imp_b1 is not None

    # Client A views (Repo 2, Ad 1) -> distinct repo, allowed!
    imp_a2 = record_impression(db_session, ad1.id, repo2.id, client_a_ip, ua)
    assert imp_a2 is not None

    # Client A views (Repo 1, Ad 2) -> distinct ad, allowed!
    imp_a_ad2 = record_impression(db_session, ad2.id, repo1.id, client_a_ip, ua)
    assert imp_a_ad2 is not None

    total = db_session.query(Impression).count()
    assert total == 4


# =====================================================================
# 5. Camo Proxy IP Extraction & UA Detection
# =====================================================================

def _make_mock_request(headers: dict, client_host: str = "127.0.0.1") -> Request:
    """Helper to construct lightweight ASGI Request for header extraction tests."""
    raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": raw_headers,
        "client": (client_host, 12345),
    }
    return Request(scope)


def test_camo_proxy_leftmost_ip_extraction():
    """
    Empirical challenge: extract_client_ip correctly extracts the leftmost origin IP
    from X-Forwarded-For header, even with multiple proxies or whitespace.
    """
    # 1. Simple single IP
    r1 = _make_mock_request({"x-forwarded-for": "203.0.113.195"})
    assert extract_client_ip(r1) == "203.0.113.195"

    # 2. Leftmost IP with chain of forwarders
    r2 = _make_mock_request({"x-forwarded-for": "203.0.113.195, 70.41.3.18, 140.82.112.4"})
    assert extract_client_ip(r2) == "203.0.113.195"

    # 3. Leading and trailing whitespace around leftmost IP
    r3 = _make_mock_request({"x-forwarded-for": "   198.51.100.42   , 10.0.0.1"})
    assert extract_client_ip(r3) == "198.51.100.42"

    # 4. Fallback to X-Real-IP when X-Forwarded-For empty
    r4 = _make_mock_request({"x-forwarded-for": "", "x-real-ip": "192.0.2.88"})
    assert extract_client_ip(r4) == "192.0.2.88"

    # 5. Fallback to request.client.host when headers missing
    r5 = _make_mock_request({}, client_host="10.20.30.40")
    assert extract_client_ip(r5) == "10.20.30.40"

    # 6. Fallback to 127.0.0.1 when request is None
    assert extract_client_ip(None) == "127.0.0.1"


def test_camo_user_agent_and_via_detection():
    """
    Empirical challenge: is_camo_request detects GitHub Camo proxy via UA and Via headers.
    """
    # Standard GitHub Camo User-Agent
    r1 = _make_mock_request({"user-agent": "github-camo (0469b82)"})
    assert is_camo_request(r1) is True

    # Generic Camo proxy
    r2 = _make_mock_request({"user-agent": "Camo Asset Proxy 2.2.0"})
    assert is_camo_request(r2) is True

    # Via header specifying camo
    r3 = _make_mock_request({"user-agent": "CustomAgent/1.0", "via": "1.1 camo"})
    assert is_camo_request(r3) is True

    # Standard browser requests (not Camo)
    r4 = _make_mock_request({"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    assert is_camo_request(r4) is False

    # False-positive vulnerability: non-camo UA containing substring 'camo'
    # e.g., 'ScamOperation/1.0' currently falsely triggers Camo detection due to '"camo" in ua'
    r5 = _make_mock_request({"user-agent": "ScamOperation/1.0"})
    assert is_camo_request(r5) is False, "False positive Camo detection on substring 'camo' in 'ScamOperation/1.0'"

    # None request
    assert is_camo_request(None) is False



def test_camo_proxy_badge_endpoint_deduplication_flow(client: TestClient, db_session):
    """
    Empirical challenge: End-to-end badge view requests through Camo proxy:
    - Two views with same leftmost origin IP within 1 hour produce exactly 1 impression.
    - Third view with different leftmost origin IP produces a second distinct impression.
    """
    repo = create_test_repository(db_session, owner="expressjs", name="express")
    create_test_ad(db_session, headline="Node Security Tools")

    camo_headers_client1 = {
        "user-agent": "github-camo (0469b82)",
        "x-forwarded-for": "203.0.113.50, 140.82.112.4",
        "via": "camo",
    }
    camo_headers_client2 = {
        "user-agent": "github-camo (0469b82)",
        "x-forwarded-for": "198.51.100.99, 140.82.112.4",
        "via": "camo",
    }

    # Request 1 from Client 1 via Camo
    res1 = client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers=camo_headers_client1)
    assert res1.status_code == 200

    # Request 2 from Client 1 via Camo (deduplicated)
    res2 = client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers=camo_headers_client1)
    assert res2.status_code == 200

    count_after_client1 = db_session.query(Impression).filter_by(repo_id=repo.id).count()
    assert count_after_client1 == 1

    # Request 3 from Client 2 via Camo (distinct IP)
    res3 = client.get(f"/badge/{repo.owner}/{repo.name}.svg", headers=camo_headers_client2)
    assert res3.status_code == 200

    count_after_client2 = db_session.query(Impression).filter_by(repo_id=repo.id).count()
    assert count_after_client2 == 2
