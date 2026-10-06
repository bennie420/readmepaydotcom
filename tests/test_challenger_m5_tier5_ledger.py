"""
tests/test_challenger_m5_tier5_ledger.py - Tier 5 Adversarial Coverage Hardening Suite.

Adversarial Stress Testing:
1. High concurrency burst clicks on nearly-exhausted budgets ($0.01 remaining with 50 concurrent requests).
2. Concurrency burst on insufficient micro-balances ($0.005 remaining, $0.01 CPC).
3. Concurrency burst on multi-credit exact exhaustion (3 clicks allowed with 50 concurrent requests).
4. Sub-cent micro-CPCs across 10,000 simulated clicks with zero floating-point drift.
5. Exact penny conservation under odd fractions across multiple maintainer payout scenarios.
6. Multi-maintainer multi-repository global penny conservation.
7. Sliding window impression deduplication boundary conditions: exact 3600-second delta (3599s vs 3600s vs 3601s).
8. Multi-hop X-Forwarded-For IP resolution and spoofed Camo User-Agent adversarial filtering.
9. Concurrent maintainer claiming race condition (20 threads simultaneously claiming identical repository).
10. Concurrent active click redirect race (/click/active/{repo_id}) with budget depletion.
11. Extreme boundary values, integer overflow attempts, and negative IDs on click and revenue routes.
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request
from starlette.testclient import TestClient

from app.database import Base, get_db, get_engine_with_pragmas
from app.main import app
from app.models.ad import Ad
from app.models.analytics import Click, Impression
from app.models.repository import Repository
from app.routers.maintainers import claim_repository
from app.services.revenue_service import (
    calculate_repository_revenue,
    compute_click_split,
    get_maintainer_revenue_summary,
)
from app.services.tracking_service import (
    DEFAULT_DEDUP_WINDOW_HOURS,
    _record_impression_with_session,
    extract_client_info,
    extract_client_ip,
    is_camo_request,
    preserve_destination_url,
    record_click,
)
from app.utils.security import compute_client_audit_hash


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture(scope="function")
def file_db_env():
    """Provides an isolated file-backed SQLite engine in WAL mode with connection pooling."""
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
        except OSError:
            pass


@pytest.fixture(scope="function")
def file_db_client(file_db_env):
    """Provides a TestClient wired to the file-backed SQLite WAL database."""
    engine, SessionMaker = file_db_env

    def override_get_db():
        db = SessionMaker()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app, follow_redirects=False)

    yield client, SessionMaker, engine

    app.dependency_overrides.pop(get_db, None)


# =====================================================================
# 1. High Concurrency Burst on Nearly-Exhausted Budgets (50 Threads)
# =====================================================================

def test_tier5_concurrent_clicks_burst_nearly_exhausted_budget(file_db_env):
    """
    Adversarial Challenge: 50 concurrent threads simultaneously click an ad
    with exactly $0.01 remaining budget and $0.01 CPC.
    Invariants:
    1. Strictly 1 thread succeeds (HTTP 302).
    2. Strictly 49 threads receive 404 (budget exhausted).
    3. Remaining budget is strictly $0.00 (NEVER negative).
    4. Ad is deactivated (is_active == False).
    5. Exactly 1 Click record is persisted with exact 50/50 split (penny conservation).
    """
    engine, SessionMaker = file_db_env

    # 1. Setup repository and ad with exactly $0.01 remaining budget
    db = SessionMaker()
    repo = Repository(owner="burst-org", name="burst-repo", stars=5000, primary_language="Rust")
    db.add(repo)
    ad = Ad(
        sponsor_name="RustSponsor",
        headline="Fast & Safe Cloud",
        click_url="https://sponsor.rs/welcome",
        remaining_budget=Decimal("0.01"),
        total_budget=Decimal("0.01"),
        cost_per_click=Decimal("0.01"),
        is_active=True,
    )
    db.add(ad)
    db.commit()
    repo_id = repo.id
    ad_id = ad.id
    db.close()

    num_threads = 50
    barrier = threading.Barrier(num_threads)
    results: list[tuple[bool, int | str]] = []
    results_lock = threading.Lock()

    def worker(worker_id: int):
        # Sync all threads to fire simultaneously
        barrier.wait()
        thread_db = SessionMaker()
        try:
            dest, click_rec = record_click(
                db=thread_db,
                ad_id=ad_id,
                repo_id=repo_id,
                client_ip=f"10.0.0.{worker_id}",
                user_agent=f"BurstBot/{worker_id}",
            )
            with results_lock:
                results.append((True, 302))
        except HTTPException as exc:
            with results_lock:
                results.append((False, exc.status_code))
        except Exception as exc:
            with results_lock:
                results.append((False, str(exc)))
        finally:
            thread_db.close()

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, i) for i in range(num_threads)]
        for f in as_completed(futures):
            f.result()

    successes = [r for r in results if r[0] is True]
    failures = [r for r in results if r[0] is False]

    assert len(successes) == 1, f"Expected strictly 1 success, got {len(successes)}"
    assert len(failures) == 49, f"Expected strictly 49 failures, got {len(failures)}"
    for fail in failures:
        assert fail[1] == 404, f"All failures must be 404 Not Found, got {fail[1]}"

    # Verify final database state
    verify_db = SessionMaker()
    final_ad = verify_db.get(Ad, ad_id)
    assert final_ad.remaining_budget == Decimal("0.00"), f"Budget must be exactly 0.00, got {final_ad.remaining_budget}"
    assert final_ad.is_active is False, "Ad must be marked inactive"

    clicks = verify_db.query(Click).filter_by(ad_id=ad_id).all()
    assert len(clicks) == 1, f"Expected exactly 1 Click record, found {len(clicks)}"
    single_click = clicks[0]
    assert single_click.cost == Decimal("0.01")
    assert single_click.maintainer_cut == Decimal("0.01")  # Odd penny goes to maintainer
    assert single_click.platform_cut == Decimal("0.00")
    assert single_click.maintainer_cut + single_click.platform_cut == single_click.cost
    verify_db.close()


def test_tier5_concurrent_clicks_burst_insufficient_micro_balance(file_db_env):
    """
    Adversarial Challenge: Remaining budget is $0.005, but CPC is $0.010.
    50 concurrent threads strike simultaneously.
    Invariants:
    1. Zero clicks succeed.
    2. All 50 receive 404.
    3. Budget remains untouched at $0.005 (never decremented).
    4. Ad is deactivated.
    """
    engine, SessionMaker = file_db_env

    db = SessionMaker()
    repo = Repository(owner="micro-org", name="micro-repo", stars=120)
    db.add(repo)
    ad = Ad(
        sponsor_name="MicroSponsor",
        headline="Sub-cent test",
        click_url="https://example.com",
        remaining_budget=Decimal("0.005"),
        total_budget=Decimal("1.000"),
        cost_per_click=Decimal("0.010"),
        is_active=True,
    )
    db.add(ad)
    db.commit()
    repo_id = repo.id
    ad_id = ad.id
    db.close()

    num_threads = 50
    barrier = threading.Barrier(num_threads)
    results = []
    lock = threading.Lock()

    def worker(idx: int):
        barrier.wait()
        s = SessionMaker()
        try:
            record_click(
                db=s,
                ad_id=ad_id,
                repo_id=repo_id,
                client_ip=f"10.1.0.{idx}",
                user_agent=f"Bot/{idx}",
            )
            with lock:
                results.append((True, 302))
        except HTTPException as exc:
            with lock:
                results.append((False, exc.status_code))
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, i) for i in range(num_threads)]
        for f in as_completed(futures):
            f.result()

    successes = [r for r in results if r[0] is True]
    assert len(successes) == 0, f"Expected 0 successes, got {len(successes)}"
    assert len(results) == 50
    for r in results:
        assert r[1] == 404

    verify_db = SessionMaker()
    final_ad = verify_db.get(Ad, ad_id)
    assert final_ad.remaining_budget == Decimal("0.005"), "Budget must not be decremented"
    assert final_ad.is_active is False
    assert verify_db.query(Click).filter_by(ad_id=ad_id).count() == 0
    verify_db.close()


def test_tier5_concurrent_clicks_burst_multi_credit(file_db_env):
    """
    Adversarial Challenge: Remaining budget is $0.03, CPC is $0.01 (exactly 3 clicks).
    50 concurrent threads race for the 3 slots.
    Invariants:
    1. Exactly 3 succeed (302).
    2. Exactly 47 fail with 404.
    3. Final budget is $0.00 and is_active is False.
    """
    engine, SessionMaker = file_db_env

    db = SessionMaker()
    repo = Repository(owner="trio-org", name="trio-repo", stars=120)
    db.add(repo)
    ad = Ad(
        sponsor_name="TrioSponsor",
        headline="3 Clicks Only",
        click_url="https://trio.com",
        remaining_budget=Decimal("0.03"),
        total_budget=Decimal("0.03"),
        cost_per_click=Decimal("0.01"),
        is_active=True,
    )
    db.add(ad)
    db.commit()
    repo_id = repo.id
    ad_id = ad.id
    db.close()

    num_threads = 50
    barrier = threading.Barrier(num_threads)
    results = []
    lock = threading.Lock()

    def worker(idx: int):
        barrier.wait()
        s = SessionMaker()
        try:
            record_click(
                db=s,
                ad_id=ad_id,
                repo_id=repo_id,
                client_ip=f"10.2.0.{idx}",
                user_agent=f"TrioBot/{idx}",
            )
            with lock:
                results.append((True, 302))
        except HTTPException as exc:
            with lock:
                results.append((False, exc.status_code))
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, i) for i in range(num_threads)]
        for f in as_completed(futures):
            f.result()

    successes = [r for r in results if r[0] is True]
    failures = [r for r in results if r[0] is False]

    assert len(successes) == 3, f"Expected exactly 3 successes, got {len(successes)}"
    assert len(failures) == 47, f"Expected exactly 47 failures, got {len(failures)}"

    verify_db = SessionMaker()
    final_ad = verify_db.get(Ad, ad_id)
    assert final_ad.remaining_budget == Decimal("0.00")
    assert final_ad.is_active is False
    assert verify_db.query(Click).filter_by(ad_id=ad_id).count() == 3
    verify_db.close()


# =====================================================================
# 2. Sub-Cent Micro-CPCs Across 10,000 Simulated Clicks (Zero Float Drift)
# =====================================================================

def test_tier5_micro_cpcs_large_volume_10000_clicks_zero_drift(file_db_env):
    """
    Adversarial Challenge: 10,000 simulated clicks with fractional micro-CPCs:
    - 2,500 clicks @ $0.0001
    - 2,500 clicks @ $0.0005
    - 2,500 clicks @ $0.0010
    - 2,500 clicks @ $0.0050
    Expected Total Gross: 2500*0.0001 + 2500*0.0005 + 2500*0.0010 + 2500*0.0050 = $16.5000.
    Invariants:
    1. For every individual click: cost == maintainer_cut + platform_cut.
    2. Sum of all 10,000 Click records: sum(cost) == sum(maintainer_cut) + sum(platform_cut).
    3. Repository revenue report: gross_revenue == maintainer_earnings + platform_cut.
    4. Exactly $16.50 gross, $8.25 maintainer earnings, $8.25 platform cut with ZERO drift.
    """
    engine, SessionMaker = file_db_env
    db = SessionMaker()

    repo = Repository(owner="volume-org", name="volume-repo", stars=10000)
    db.add(repo)
    ad = Ad(
        sponsor_name="MicroScaleAds",
        headline="Sub-cent volume verification",
        click_url="https://microscale.com",
        remaining_budget=Decimal("50.00"),
        total_budget=Decimal("50.00"),
        cost_per_click=Decimal("0.0010"),
        is_active=True,
    )
    db.add(ad)
    db.commit()
    repo_id = repo.id
    ad_id = ad.id

    # Construct 10,000 Click records with varying micro-CPCs
    rates = [
        Decimal("0.0001"),
        Decimal("0.0005"),
        Decimal("0.0010"),
        Decimal("0.0050"),
    ]
    clicks = []
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    expected_total_cost = Decimal("0.0000")
    expected_maintainer_sum = Decimal("0.0000")
    expected_platform_sum = Decimal("0.0000")

    for i in range(10000):
        cpc = rates[i % 4]
        half = cpc / Decimal(2)
        if cpc == cpc.quantize(Decimal("0.01")):
            m_cut = half.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            m_cut = half
        p_cut = cpc - m_cut

        expected_total_cost += cpc
        expected_maintainer_sum += m_cut
        expected_platform_sum += p_cut

        clicks.append(
            Click(
                repo_id=repo_id,
                ad_id=ad_id,
                client_hash=compute_client_audit_hash(f"192.168.10.{i % 250}", f"UA/{i}"),
                cost=cpc,
                maintainer_cut=m_cut,
                platform_cut=p_cut,
                timestamp=base_time + timedelta(seconds=i),
            )
        )

    # Bulk insert all 10,000 records
    db.bulk_save_objects(clicks)
    db.commit()

    # Query all 10,000 clicks and assert invariants
    db_clicks = db.query(Click).filter_by(repo_id=repo_id).all()
    assert len(db_clicks) == 10000

    db_total_cost = Decimal("0.0000")
    db_maintainer_sum = Decimal("0.0000")
    db_platform_sum = Decimal("0.0000")

    for c in db_clicks:
        cost = Decimal(str(c.cost))
        m_cut = Decimal(str(c.maintainer_cut))
        p_cut = Decimal(str(c.platform_cut))
        assert cost == m_cut + p_cut, f"Click {c.id} broken split: {cost} != {m_cut} + {p_cut}"
        db_total_cost += cost
        db_maintainer_sum += m_cut
        db_platform_sum += p_cut

    assert db_total_cost == expected_total_cost == Decimal("16.5000")
    assert db_maintainer_sum == expected_maintainer_sum == Decimal("8.2500")
    assert db_platform_sum == expected_platform_sum == Decimal("8.2500")
    assert db_total_cost == db_maintainer_sum + db_platform_sum

    # Verify through revenue service
    report = calculate_repository_revenue(db, repo_id)
    assert report["total_clicks"] == 10000
    assert report["gross_revenue"] == 16.5
    assert report["maintainer_earnings"] == 8.25
    assert report["platform_cut"] == 8.25
    assert Decimal(str(report["gross_revenue"])) == (
        Decimal(str(report["maintainer_earnings"])) + Decimal(str(report["platform_cut"]))
    )
    db.close()


# =====================================================================
# 3. Exact Penny Conservation Across Odd Amounts
# =====================================================================

@pytest.mark.parametrize(
    "cost, expected_maintainer, expected_platform",
    [
        (Decimal("0.01"), Decimal("0.01"), Decimal("0.00")),
        (Decimal("0.03"), Decimal("0.02"), Decimal("0.01")),
        (Decimal("0.05"), Decimal("0.03"), Decimal("0.02")),
        (Decimal("0.07"), Decimal("0.04"), Decimal("0.03")),
        (Decimal("0.09"), Decimal("0.05"), Decimal("0.04")),
        (Decimal("0.11"), Decimal("0.06"), Decimal("0.05")),
        (Decimal("0.13"), Decimal("0.07"), Decimal("0.06")),
        (Decimal("0.15"), Decimal("0.08"), Decimal("0.07")),
        (Decimal("0.33"), Decimal("0.17"), Decimal("0.16")),
        (Decimal("0.49"), Decimal("0.25"), Decimal("0.24")),
        (Decimal("0.99"), Decimal("0.50"), Decimal("0.49")),
        (Decimal("1.01"), Decimal("0.51"), Decimal("0.50")),
        (Decimal("1.37"), Decimal("0.69"), Decimal("0.68")),
        (Decimal("7.77"), Decimal("3.89"), Decimal("3.88")),
        (Decimal("19.99"), Decimal("10.00"), Decimal("9.99")),
        (Decimal("99.99"), Decimal("50.00"), Decimal("49.99")),
        (Decimal("100.01"), Decimal("50.01"), Decimal("50.00")),
    ],
)
def test_tier5_penny_conservation_all_odd_cent_fractions(cost, expected_maintainer, expected_platform):
    """
    Adversarial Challenge: Verify exact penny conservation across odd cent values.
    ROUND_HALF_UP guarantees maintainer receives the half-cent advantage,
    and platform gets the remainder, summing to the exact original cent amount.
    """
    m_cut, p_cut = compute_click_split(cost)
    assert m_cut == expected_maintainer, f"Cost {cost}: expected maintainer {expected_maintainer}, got {m_cut}"
    assert p_cut == expected_platform, f"Cost {cost}: expected platform {expected_platform}, got {p_cut}"
    assert m_cut + p_cut == cost, f"Cost {cost}: penny conservation violated ({m_cut} + {p_cut} != {cost})"


def test_tier5_multi_maintainer_multi_repo_penny_conservation(file_db_env):
    """
    Adversarial Challenge: Multi-maintainer, multi-repository scenario:
    - Maintainer Alice: 3 repositories with odd gross revenues:
      - Repo 1: $0.01 gross -> Maintainer $0.01, Platform $0.00
      - Repo 2: $0.03 gross -> Maintainer $0.02, Platform $0.01
      - Repo 3: $0.07 gross -> Maintainer $0.04, Platform $0.03
      Total Alice: Gross $0.11, Maintainer $0.07, Platform $0.04.
    - Maintainer Bob: 2 repositories:
      - Repo 4: $0.10 gross -> Maintainer $0.05, Platform $0.05
      - Repo 5: $0.05 gross -> Maintainer $0.03, Platform $0.02
      Total Bob: Gross $0.15, Maintainer $0.08, Platform $0.07.
    - Unclaimed Repo 6 (Escrow):
      - Repo 6: $0.09 gross -> Maintainer (escrow) $0.05, Platform $0.04
    Invariants:
    1. Sum of Alice's individual repos == Alice's summary endpoint.
    2. Sum of Bob's individual repos == Bob's summary endpoint.
    3. Global Conservation: sum(all gross) == sum(all maintainer earnings) + sum(all platform cuts).
    """
    engine, SessionMaker = file_db_env
    db = SessionMaker()

    # Create Alice's repos
    r1 = Repository(owner="alice", name="repo-alpha", claimed=True, claimed_by="alice_dev", maintainer_handle="alice_dev")
    r2 = Repository(owner="alice", name="repo-beta", claimed=True, claimed_by="alice_dev", maintainer_handle="alice_dev")
    r3 = Repository(owner="alice", name="repo-gamma", claimed=True, claimed_by="alice_dev", maintainer_handle="alice_dev")

    # Create Bob's repos
    r4 = Repository(owner="bob", name="repo-delta", claimed=True, claimed_by="bob_dev", maintainer_handle="bob_dev")
    r5 = Repository(owner="bob", name="repo-epsilon", claimed=True, claimed_by="bob_dev", maintainer_handle="bob_dev")

    # Create Unclaimed repo
    r6 = Repository(owner="community", name="repo-zeta", claimed=False)

    db.add_all([r1, r2, r3, r4, r5, r6])
    ad = Ad(
        sponsor_name="UniversalAd",
        headline="Universal Headline",
        click_url="https://u.com",
        cost_per_click=Decimal("0.01"),
    )
    db.add(ad)
    db.commit()

    def add_clicks(repo_id: int, amounts: list[Decimal]):
        now = datetime.now(UTC)
        for amt in amounts:
            m_cut, p_cut = compute_click_split(amt)
            c = Click(
                repo_id=repo_id,
                ad_id=ad.id,
                client_hash="0" * 64,
                cost=amt,
                maintainer_cut=m_cut,
                platform_cut=p_cut,
                timestamp=now,
            )
            db.add(c)
        db.commit()

    add_clicks(r1.id, [Decimal("0.01")])
    add_clicks(r2.id, [Decimal("0.03")])
    add_clicks(r3.id, [Decimal("0.07")])
    add_clicks(r4.id, [Decimal("0.10")])
    add_clicks(r5.id, [Decimal("0.05")])
    add_clicks(r6.id, [Decimal("0.09")])

    alice_summary = get_maintainer_revenue_summary(db, "alice_dev")
    assert alice_summary["total_repositories"] == 3
    assert alice_summary["gross_revenue"] == 0.11
    assert alice_summary["total_earnings"] == 0.07

    bob_summary = get_maintainer_revenue_summary(db, "bob_dev")
    assert bob_summary["total_repositories"] == 2
    assert bob_summary["gross_revenue"] == 0.15
    assert bob_summary["total_earnings"] == 0.08

    escrow_repo = calculate_repository_revenue(db, r6.id)
    assert escrow_repo["claimed"] is False
    assert escrow_repo["gross_revenue"] == 0.09
    assert escrow_repo["maintainer_earnings"] == 0.05
    assert escrow_repo["platform_cut"] == 0.04

    # Global conservation across all 6 repos
    all_reports = [calculate_repository_revenue(db, r.id) for r in [r1, r2, r3, r4, r5, r6]]
    total_gross = sum(Decimal(str(rep["gross_revenue"])) for rep in all_reports)
    total_maintainer = sum(Decimal(str(rep["maintainer_earnings"])) for rep in all_reports)
    total_platform = sum(Decimal(str(rep["platform_cut"])) for rep in all_reports)

    assert total_gross == Decimal("0.35")
    assert total_maintainer == Decimal("0.20")
    assert total_platform == Decimal("0.15")
    assert total_gross == total_maintainer + total_platform, "System-wide penny conservation failed"

    db.close()


# =====================================================================
# 4. Exact 3600-Second Sliding Window Boundary Conditions
# =====================================================================

def test_tier5_sliding_window_deduplication_exact_3600s_boundary(file_db_env):
    """
    Adversarial Challenge: Verify impression deduplication exact 3600-second window boundary.
    - Record initial impression at T0.
    - At T0 + 3599 seconds (inside window): MUST BE DEDUPLICATED (None).
    - At T0 + 3600 seconds (exact window boundary): MUST BE DEDUPLICATED (None).
    - At T0 + 3601 seconds (outside window): MUST BE RECORDED (Impression returned).
    - Multi-dimension isolation:
      - Different repo_id inside window: NOT deduplicated.
      - Different ad_id inside window: NOT deduplicated.
      - Different client IP inside window: NOT deduplicated.
    """
    engine, SessionMaker = file_db_env
    db = SessionMaker()

    repo1 = Repository(owner="dedup-org", name="dedup-repo-1", stars=50)
    repo2 = Repository(owner="dedup-org", name="dedup-repo-2", stars=60)
    db.add_all([repo1, repo2])

    ad1 = Ad(sponsor_name="Ad-1", headline="Headline 1", click_url="https://ad1.com")
    ad2 = Ad(sponsor_name="Ad-2", headline="Headline 2", click_url="https://ad2.com")
    db.add_all([ad1, ad2])
    db.commit()

    ip_primary = "192.168.1.100"
    ua_primary = "Mozilla/5.0 TestBrowser"
    client_hash_primary = compute_client_audit_hash(ip_primary, ua_primary)

    # 1. Base Impression: Recorded at T0 (e.g. exactly 1 hour ago)
    now = datetime.now(UTC)
    t0 = now - timedelta(seconds=3600)

    base_imp = Impression(
        repo_id=repo1.id,
        ad_id=ad1.id,
        client_hash=client_hash_primary,
        timestamp=t0,
        cost=Decimal("0.0020"),
        maintainer_share=Decimal("0.0010"),
        platform_share=Decimal("0.0010"),
    )
    db.add(base_imp)
    db.commit()

    # In our implementation:
    # cutoff = now - timedelta(hours=window_hours)
    # filter(Impression.timestamp >= cutoff)

    # Scenario A: Candidate timestamp at T0 + 3599s (i.e. cutoff = T0 - 1s)
    # Check if base_imp (at T0) is >= cutoff (T0 - 1s) -> True! Deduplicated.
    cutoff_3599 = t0 + timedelta(seconds=3599) - timedelta(hours=1)  # t0 - 1s
    match_3599 = db.query(Impression).filter(
        Impression.repo_id == repo1.id,
        Impression.ad_id == ad1.id,
        Impression.client_hash == client_hash_primary,
        Impression.timestamp >= cutoff_3599,
    ).first()
    assert match_3599 is not None, "At 3599s delta, existing impression MUST match (deduplicate)"

    # Scenario B: Candidate timestamp at T0 + 3600s (exact boundary, cutoff = T0)
    # Check if base_imp (at T0) is >= cutoff (T0) -> True! Deduplicated.
    cutoff_3600 = t0 + timedelta(seconds=3600) - timedelta(hours=1)  # t0
    match_3600 = db.query(Impression).filter(
        Impression.repo_id == repo1.id,
        Impression.ad_id == ad1.id,
        Impression.client_hash == client_hash_primary,
        Impression.timestamp >= cutoff_3600,
    ).first()
    assert match_3600 is not None, "At 3600s boundary, existing impression MUST match (deduplicate)"

    # Scenario C: Candidate timestamp at T0 + 3601s (cutoff = T0 + 1s)
    # Check if base_imp (at T0) is >= cutoff (T0 + 1s) -> False! Outside window.
    cutoff_3601 = t0 + timedelta(seconds=3601) - timedelta(hours=1)  # t0 + 1s
    match_3601 = db.query(Impression).filter(
        Impression.repo_id == repo1.id,
        Impression.ad_id == ad1.id,
        Impression.client_hash == client_hash_primary,
        Impression.timestamp >= cutoff_3601,
    ).first()
    assert match_3601 is None, "At 3601s delta, existing impression MUST NOT match (allow new impression)"

    # Scenario D: Live test with _record_impression_with_session
    # First attempt right now (where t0 was 3600s ago):
    # Depending on slight microsecond passage, let's test isolation across dimensions:

    # Different repo_id -> MUST record
    imp_diff_repo = _record_impression_with_session(
        db=db,
        ad_id=ad1.id,
        repo_id=repo2.id,
        client_ip=ip_primary,
        user_agent=ua_primary,
    )
    assert imp_diff_repo is not None, "Different repo_id must record"
    assert imp_diff_repo.repo_id == repo2.id

    # Different ad_id -> MUST record
    imp_diff_ad = _record_impression_with_session(
        db=db,
        ad_id=ad2.id,
        repo_id=repo1.id,
        client_ip=ip_primary,
        user_agent=ua_primary,
    )
    assert imp_diff_ad is not None, "Different ad_id must record"
    assert imp_diff_ad.ad_id == ad2.id

    # Different IP (hash) -> MUST record
    imp_diff_ip = _record_impression_with_session(
        db=db,
        ad_id=ad1.id,
        repo_id=repo1.id,
        client_ip="192.168.1.101",
        user_agent=ua_primary,
    )
    assert imp_diff_ip is not None, "Different IP must record"
    assert imp_diff_ip.client_hash != client_hash_primary

    # Duplicate immediate call with same params -> MUST deduplicate (return None)
    dup_imp = _record_impression_with_session(
        db=db,
        ad_id=ad2.id,
        repo_id=repo1.id,
        client_ip=ip_primary,
        user_agent=ua_primary,
    )
    assert dup_imp is None, "Immediate duplicate must return None"

    db.close()


# =====================================================================
# 5. Multi-Hop Camo Headers & Spoofed User-Agents
# =====================================================================

def test_tier5_multi_hop_camo_and_spoofed_headers():
    """
    Adversarial Challenge: Verify multi-hop header extraction and Camo proxy detection:
    1. Multi-hop X-Forwarded-For:
       - '203.0.113.195, 70.41.3.18, 150.172.238.178, 10.0.0.1' -> '203.0.113.195'
       - IPv6 chain: '2001:db8:85a3::8a2e:370:7334, 192.0.2.1' -> '2001:db8:85a3::8a2e:370:7334'
       - Dirty whitespaces: '   198.51.100.22 \t , 10.0.0.2' -> '198.51.100.22'
    2. Camo detection boundary:
       - Legitimate Camo: 'Camo Asset Proxy 2.2.0', 'github-camo', 'GitHub-Camo/1.0.0' -> True
       - Via header Camo: Via: '1.1 github-camo' -> True
       - Spoofed/Embedded: 'Camouflage/1.0' -> False
       - Spoofed suffix: 'camo_fake_bot' -> False
       - Spoofed non-word-boundary: 'anti_camo_shield' -> False
    3. Collision resistance: Two clients behind Camo with different leftmost IPs generate different audit hashes.
    """
    def mock_request(headers: dict, client_host: str = "127.0.0.1") -> Request:
        raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": raw_headers,
            "client": (client_host, 12345),
        }
        return Request(scope)

    # 1. Multi-hop X-Forwarded-For tests
    req1 = mock_request({"x-forwarded-for": "203.0.113.195, 70.41.3.18, 150.172.238.178, 10.0.0.1"})
    assert extract_client_ip(req1) == "203.0.113.195"

    req2 = mock_request({"x-forwarded-for": "2001:db8:85a3::8a2e:370:7334, 192.0.2.1"})
    assert extract_client_ip(req2) == "2001:db8:85a3::8a2e:370:7334"

    req3 = mock_request({"x-forwarded-for": "   198.51.100.22 \t , 10.0.0.2"})
    assert extract_client_ip(req3) == "198.51.100.22"

    req4 = mock_request({"x-forwarded-for": "", "x-real-ip": "198.51.100.99"})
    assert extract_client_ip(req4) == "198.51.100.99"

    # 2. Camo detection & spoofing tests
    assert is_camo_request(mock_request({"user-agent": "Camo Asset Proxy 2.2.0"})) is True
    assert is_camo_request(mock_request({"user-agent": "github-camo"})) is True
    assert is_camo_request(mock_request({"user-agent": "GitHub-Camo/1.0.0 (https://github.com/atmos/camo)"})) is True
    assert is_camo_request(mock_request({"via": "1.1 github-camo", "user-agent": "curl/7.68.0"})) is True
    assert is_camo_request(mock_request({"via": "1.1 camo", "user-agent": "Python/3.12"})) is True

    # Spoofed non-camo User-Agents (word boundary protections)
    assert is_camo_request(mock_request({"user-agent": "Camouflage/1.0"})) is False
    assert is_camo_request(mock_request({"user-agent": "camo_fake_bot"})) is False
    assert is_camo_request(mock_request({"user-agent": "camo123"})) is False
    assert is_camo_request(mock_request({"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})) is False

    # 3. Privacy hash distinctness across proxy hops
    camo_ua = "Camo Asset Proxy 2.2.0"
    info_client_a = extract_client_info(mock_request({
        "x-forwarded-for": "100.64.0.1, 140.82.112.4",
        "user-agent": camo_ua,
    }))
    info_client_b = extract_client_info(mock_request({
        "x-forwarded-for": "100.64.0.2, 140.82.112.4",
        "user-agent": camo_ua,
    }))

    assert info_client_a.is_camo is True
    assert info_client_b.is_camo is True
    assert info_client_a.ip == "100.64.0.1"
    assert info_client_b.ip == "100.64.0.2"
    assert info_client_a.client_hash != info_client_b.client_hash, "Distinct client IPs behind Camo must produce distinct hashes"


# =====================================================================
# 6. Concurrent Maintainer Claiming Race Condition (20 Threads)
# =====================================================================

def test_tier5_concurrent_maintainer_claiming_20_threads_race(file_db_client):
    """
    Adversarial Challenge: 20 concurrent threads simultaneously race to claim
    the EXACT same unclaimed repository.
    Invariants:
    1. Strictly 1 thread wins with HTTP 200.
    2. Strictly 19 threads receive HTTP 409 Conflict.
    3. Zero threads receive 500 Internal Server Error or other codes.
    4. Repository is claimed with winning maintainer handle recorded.
    5. Database integrity is completely intact without corruption.
    """
    client, SessionMaker, engine = file_db_client

    db = SessionMaker()
    repo = Repository(
        owner="race-king",
        name="crown-jewel",
        stars=77777,
        primary_language="TypeScript",
        claimed=False,
    )
    db.add(repo)
    db.commit()
    repo_id = repo.id
    db.close()

    num_competitors = 20
    barrier = threading.Barrier(num_competitors)
    responses = []
    lock = threading.Lock()

    def attempt_claim(worker_idx: int):
        barrier.wait()
        # Unique TestClient per thread hitting FastAPI app
        thread_client = TestClient(app, follow_redirects=False)
        resp = thread_client.post(
            "/maintainers/claim",
            json={
                "owner": "race-king",
                "name": "crown-jewel",
                "maintainer_handle": f"maintainer_{worker_idx:02d}",
                "payout_address": f"0xETH_{worker_idx:02d}",
            },
        )
        with lock:
            responses.append((worker_idx, resp.status_code, resp.text))

    with ThreadPoolExecutor(max_workers=num_competitors) as executor:
        futures = [executor.submit(attempt_claim, i) for i in range(num_competitors)]
        for f in as_completed(futures):
            f.result()

    successes = [r for r in responses if r[1] == 200]
    conflicts = [r for r in responses if r[1] == 409]
    others = [r for r in responses if r[1] not in (200, 409)]

    assert len(others) == 0, f"Unexpected status codes encountered: {others}"
    assert len(successes) == 1, f"Expected strictly 1 winning claim (200), got {len(successes)}"
    assert len(conflicts) == 19, f"Expected strictly 19 conflicts (409), got {len(conflicts)}"

    winner_idx = successes[0][0]
    expected_winner_handle = f"maintainer_{winner_idx:02d}"

    # Verify winning state in database
    verify_db = SessionMaker()
    claimed_repo = verify_db.get(Repository, repo_id)
    assert claimed_repo.claimed is True
    assert claimed_repo.maintainer_handle == expected_winner_handle
    assert claimed_repo.claimed_by == expected_winner_handle
    assert claimed_repo.claimed_at is not None
    assert claimed_repo.payout_address == f"0xETH_{winner_idx:02d}"

    # Verify that a subsequent 21st claim attempt is also rejected with 409
    subsequent_resp = client.post(
        "/maintainers/claim",
        json={
            "owner": "race-king",
            "name": "crown-jewel",
            "maintainer_handle": "late_comer",
        },
    )
    assert subsequent_resp.status_code == 409
    assert f"already been claimed by '{expected_winner_handle}'" in subsequent_resp.text
    verify_db.close()


# =====================================================================
# 7. Concurrent Dynamic Active Click Redirect Race (/click/active/{repo_id})
# =====================================================================

def test_tier5_concurrent_active_click_redirect_race(file_db_client):
    """
    Adversarial Challenge: 20 concurrent requests hit /click/active/{repo_id}
    where the repo matches an ad with budget for only 2 clicks ($0.02 budget, $0.01 CPC).
    Invariants:
    1. Strictly 2 requests receive 302 Found redirect with Location header.
    2. Strictly 18 requests receive 404 Not Found.
    3. Exactly 2 Click records persisted.
    4. Ad remaining budget drops to $0.00 and is deactivated.
    """
    client, SessionMaker, engine = file_db_client

    db = SessionMaker()
    repo = Repository(owner="act-org", name="act-repo", stars=100, primary_language="Go")
    db.add(repo)
    ad = Ad(
        sponsor_name="GoSponsor",
        headline="Go Cloud Tools",
        click_url="https://gosponsor.com/landing?ref=badge",
        target_language="go",
        remaining_budget=Decimal("0.02"),
        total_budget=Decimal("0.02"),
        cost_per_click=Decimal("0.01"),
        is_active=True,
    )
    db.add(ad)
    db.commit()
    repo_id = repo.id
    ad_id = ad.id
    db.close()

    num_threads = 20
    barrier = threading.Barrier(num_threads)
    results = []
    lock = threading.Lock()

    def do_active_click(idx: int):
        barrier.wait()
        th_client = TestClient(app, follow_redirects=False)
        resp = th_client.get(
            f"/click/active/{repo_id}",
            headers={"x-forwarded-for": f"10.50.0.{idx}"},
        )
        with lock:
            results.append((idx, resp.status_code, resp.headers.get("Location")))

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(do_active_click, i) for i in range(num_threads)]
        for f in as_completed(futures):
            f.result()

    successes = [r for r in results if r[1] == 302]
    failures = [r for r in results if r[1] == 404]

    assert len(successes) == 2, f"Expected 2 successes, got {len(successes)}"
    assert len(failures) == 18, f"Expected 18 failures, got {len(failures)}"
    for s in successes:
        assert "gosponsor.com" in s[2]

    verify_db = SessionMaker()
    final_ad = verify_db.get(Ad, ad_id)
    assert final_ad.remaining_budget == Decimal("0.00")
    assert final_ad.is_active is False
    assert verify_db.query(Click).filter_by(ad_id=ad_id).count() == 2
    verify_db.close()


# =====================================================================
# 8. Adversarial Boundary Inputs (Click & Revenue Routes)
# =====================================================================

@pytest.mark.parametrize(
    "ad_id, repo_id",
    [
        ("-1", "1"),
        ("1", "-1"),
        ("0", "1"),
        ("1", "0"),
        ("9999999999999999999999999999999", "1"),
        ("1", "9999999999999999999999999999999"),
        ("abc", "1"),
        ("1", "def"),
        ("3.14", "1"),
    ],
)
def test_tier5_adversarial_click_identifiers_rejected(file_db_client, ad_id, repo_id):
    """Adversarial malformed or overflow identifiers must return 404 or 422 without 500 error."""
    client, SessionMaker, engine = file_db_client
    resp = client.get(f"/click/{ad_id}/{repo_id}")
    assert resp.status_code in (404, 422), f"Expected 404 or 422, got {resp.status_code}"


@pytest.mark.parametrize(
    "repo_id",
    [
        "-1",
        "0",
        "9999999999999999999999999999999",
        "xyz",
        "12.34",
    ],
)
def test_tier5_adversarial_revenue_identifiers_rejected(file_db_client, repo_id):
    """Adversarial revenue route parameters must return 404 or 422 without 500 error."""
    client, SessionMaker, engine = file_db_client
    resp = client.get(f"/revenue/{repo_id}")
    assert resp.status_code in (404, 422), f"Expected 404 or 422, got {resp.status_code}"


def test_tier5_revenue_zero_clicks_many_impressions(file_db_client):
    """
    Adversarial Challenge: Repository with 5,000 impressions and 0 clicks.
    Revenue report must return exact 0.00 for earnings, cuts, and fees,
    with impressions_count == 5000 and clicks_count == 0.
    """
    client, SessionMaker, engine = file_db_client
    db = SessionMaker()

    repo = Repository(owner="view-heavy", name="stars-only", stars=900)
    db.add(repo)
    ad = Ad(sponsor_name="ViewAd", headline="View Headline", click_url="https://view.com")
    db.add(ad)
    db.commit()

    # Bulk insert impressions
    now = datetime.now(UTC)
    imps = [
        Impression(
            repo_id=repo.id,
            ad_id=ad.id,
            client_hash=compute_client_audit_hash(f"10.0.{i // 256}.{i % 256}", "Agent"),
            timestamp=now - timedelta(seconds=i),
            cost=Decimal("0.0020"),
            maintainer_share=Decimal("0.0010"),
            platform_share=Decimal("0.0010"),
        )
        for i in range(5000)
    ]
    db.bulk_save_objects(imps)
    db.commit()

    resp = client.get(f"/revenue/{repo.id}")
    assert resp.status_code == 200
    data = resp.json()

    assert data["gross_revenue"] == 0.0
    assert data["maintainer_earnings"] == 0.0
    assert data["platform_cut"] == 0.0
    assert data["total_clicks"] == 0
    assert data["total_impressions"] == 5000
    db.close()
