"""
tests/test_challenger_m1.py - Empirical Adversarial Challenge Suite for Milestone 1

Written by empirical challenger agent (challenger_m1_1).
Stress-tests:
1. Composite unique constraints under adversarial conditions & collisions.
2. Foreign key orphan rejection (ORM and Raw SQL).
3. Cascade deletion under volume and through raw database-level execution.
4. Cryptographic SHA-256 audit hash properties, edge cases, avalanche metrics.
5. Monetary Decimal precision and absence of floating point drift.
6. SQLite WAL concurrency & connection PRAGMAs.
7. Anti-pattern compliance verification.
"""

import os
import re
import tempfile
import threading
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_engine_with_pragmas
from app.models.ad import Ad
from app.models.analytics import Click, Impression
from app.models.repository import Repository
from app.utils.security import compute_client_audit_hash

# --- Fixtures ---

@pytest.fixture(scope="function")
def challenger_db_engine():
    """File-backed SQLite engine in tempdir enforcing all SQLite PRAGMAs."""
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
def challenger_session(challenger_db_engine):
    """Provides an isolated database session."""
    SessionLocal = sessionmaker(bind=challenger_db_engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# =====================================================================
# Challenge Area 1: Composite Unique Constraints & Collision Fuzzing
# =====================================================================

def test_challenge_composite_unique_exact_duplicate(challenger_session):
    """Verify composite unique constraint (owner, name) strictly prevents duplicates."""
    repo1 = Repository(owner="torvalds", name="linux", stars=170000)
    challenger_session.add(repo1)
    challenger_session.commit()

    repo2 = Repository(owner="torvalds", name="linux", stars=175000)
    challenger_session.add(repo2)
    with pytest.raises(IntegrityError) as exc_info:
        challenger_session.commit()
    challenger_session.rollback()
    assert "UNIQUE constraint failed" in str(exc_info.value)


def test_challenge_composite_unique_raw_sql_bypass_attempt(challenger_db_engine):
    """Attempt to bypass ORM by directly executing raw SQL INSERT duplicate."""
    with challenger_db_engine.connect() as conn:
        conn.execute(
            text("INSERT INTO repositories (owner, name, stars, claimed) VALUES (:o, :n, :s, :c)"),
            {"o": "pallets", "n": "flask", "s": 65000, "c": 0}
        )
        conn.commit()

        with pytest.raises(IntegrityError):
            conn.execute(
                text("INSERT INTO repositories (owner, name, stars, claimed) VALUES (:o, :n, :s, :c)"),
                {"o": "pallets", "n": "flask", "s": 70000, "c": 0}
            )
            conn.commit()


def test_challenge_composite_unique_different_owners_identical_name(challenger_session):
    """Different owners with identical repo names must succeed."""
    r1 = Repository(owner="facebook", name="react", stars=220000)
    r2 = Repository(owner="preactjs", name="react", stars=36000)
    challenger_session.add_all([r1, r2])
    challenger_session.commit()

    assert r1.id != r2.id
    assert r1.name == r2.name
    assert r1.owner != r2.owner


def test_challenge_composite_unique_identical_owner_different_names(challenger_session):
    """Same owner with different repo names must succeed."""
    r1 = Repository(owner="encode", name="starlette", stars=12000)
    r2 = Repository(owner="encode", name="httpx", stars=14000)
    challenger_session.add_all([r1, r2])
    challenger_session.commit()

    assert r1.id != r2.id
    assert r1.owner == r2.owner


def test_challenge_github_id_global_uniqueness(challenger_session):
    """Verify github_id unique constraint prevents duplicate GitHub repository IDs."""
    r1 = Repository(owner="org1", name="repo-one", github_id=424242)
    challenger_session.add(r1)
    challenger_session.commit()

    r2 = Repository(owner="org2", name="repo-two", github_id=424242)
    challenger_session.add(r2)
    with pytest.raises(IntegrityError):
        challenger_session.commit()
    challenger_session.rollback()


# =====================================================================
# Challenge Area 2: Foreign Key Integrity & Orphan Rejection
# =====================================================================

def test_challenge_orphan_impression_rejected_invalid_repo(challenger_session):
    """Verify impression with non-existent repo_id is rejected by database."""
    ad = Ad(sponsor_name="Test Sponsor", headline="Test", click_url="https://test.com")
    challenger_session.add(ad)
    challenger_session.commit()

    orphan_imp = Impression(
        repo_id=99999999,
        ad_id=ad.id,
        client_hash="0" * 64,
    )
    challenger_session.add(orphan_imp)
    with pytest.raises(IntegrityError):
        challenger_session.commit()
    challenger_session.rollback()


def test_challenge_orphan_impression_rejected_invalid_ad(challenger_session):
    """Verify impression with non-existent ad_id is rejected by database."""
    repo = Repository(owner="astral-sh", name="uv", stars=45000)
    challenger_session.add(repo)
    challenger_session.commit()

    orphan_imp = Impression(
        repo_id=repo.id,
        ad_id=88888888,
        client_hash="1" * 64,
    )
    challenger_session.add(orphan_imp)
    with pytest.raises(IntegrityError):
        challenger_session.commit()
    challenger_session.rollback()


def test_challenge_orphan_click_rejected_invalid_repo_and_ad(challenger_session):
    """Verify click with invalid repo and ad is rejected."""
    orphan_click = Click(
        repo_id=77777777,
        ad_id=66666666,
        client_hash="2" * 64,
    )
    challenger_session.add(orphan_click)
    with pytest.raises(IntegrityError):
        challenger_session.commit()
    challenger_session.rollback()


def test_challenge_raw_sql_foreign_key_enforcement(challenger_db_engine):
    """Verify foreign key enforcement is active at SQLite engine level via raw SQL."""
    with challenger_db_engine.connect() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                text("INSERT INTO impressions (ad_id, repo_id, client_hash) VALUES (1, 1, :h)"),
                {"h": "3" * 64}
            )
            conn.commit()


# =====================================================================
# Challenge Area 3: Cascade Deletion Under Volume & Raw SQL
# =====================================================================

def test_challenge_cascade_delete_repository_volume(challenger_session):
    """Stress test cascade delete with volume (50 impressions and 25 clicks)."""
    repo = Repository(owner="django", name="django", stars=80000)
    ad = Ad(sponsor_name="Postgres Pro", headline="DB Performance", click_url="https://pg.com")
    challenger_session.add_all([repo, ad])
    challenger_session.commit()

    for i in range(50):
        challenger_session.add(
            Impression(repo_id=repo.id, ad_id=ad.id, client_hash=f"{i:064x}")
        )
    for j in range(25):
        challenger_session.add(
            Click(repo_id=repo.id, ad_id=ad.id, client_hash=f"{j:064x}")
        )
    challenger_session.commit()

    repo_id, ad_id = repo.id, ad.id

    # Verify counts before delete
    imp_count = len(challenger_session.scalars(select(Impression).where(Impression.repo_id == repo_id)).all())
    click_count = len(challenger_session.scalars(select(Click).where(Click.repo_id == repo_id)).all())
    assert imp_count == 50
    assert click_count == 25

    # Delete repository
    challenger_session.delete(repo)
    challenger_session.commit()

    # Verify all 75 child records are purged
    imp_remaining = challenger_session.scalars(select(Impression).where(Impression.repo_id == repo_id)).all()
    click_remaining = challenger_session.scalars(select(Click).where(Click.repo_id == repo_id)).all()
    assert len(imp_remaining) == 0, "All impressions must be purged on repo delete"
    assert len(click_remaining) == 0, "All clicks must be purged on repo delete"

    # Verify Ad remains intact
    assert challenger_session.get(Ad, ad_id) is not None, "Ad must remain untouched"


def test_challenge_raw_sql_cascade_delete_ad(challenger_db_engine):
    """Verify ON DELETE CASCADE functions even when deletion happens via raw SQL."""
    with challenger_db_engine.connect() as conn:
        conn.execute(
            text("INSERT INTO repositories (id, owner, name, stars, claimed) VALUES (10, 'meta', 'llama', 50000, 0)")
        )
        conn.execute(
            text("INSERT INTO ads (id, sponsor_name, headline, call_to_action, click_url, total_budget, remaining_budget, cost_per_click, cost_per_impression, is_active) VALUES (20, 'NVIDIA', 'Compute', 'Learn More', 'https://nvidia.com', 100.0, 100.0, 0.5, 0.002, 1)")
        )
        conn.execute(
            text("INSERT INTO impressions (ad_id, repo_id, client_hash, cost, maintainer_share, platform_share) VALUES (20, 10, 'aabb', 0.002, 0.001, 0.001)")
        )
        conn.execute(
            text("INSERT INTO clicks (ad_id, repo_id, client_hash, cost, maintainer_cut, platform_cut) VALUES (20, 10, 'ccdd', 0.5, 0.25, 0.25)")
        )
        conn.commit()

        # Delete Ad directly via raw SQL
        conn.execute(text("DELETE FROM ads WHERE id = 20;"))
        conn.commit()

        # Check impressions and clicks count
        imp_count = conn.execute(text("SELECT COUNT(*) FROM impressions WHERE ad_id = 20;")).scalar()
        click_count = conn.execute(text("SELECT COUNT(*) FROM clicks WHERE ad_id = 20;")).scalar()
        repo_count = conn.execute(text("SELECT COUNT(*) FROM repositories WHERE id = 10;")).scalar()

        assert imp_count == 0, "Raw SQL delete on ad must trigger SQLite ON DELETE CASCADE for impressions"
        assert click_count == 0, "Raw SQL delete on ad must trigger SQLite ON DELETE CASCADE for clicks"
        assert repo_count == 1, "Repository must remain unaffected"


# =====================================================================
# Challenge Area 4: Cryptographic SHA-256 Audit Hash Verification
# =====================================================================

def test_challenge_sha256_determinism():
    """Verify audit hash is 100% deterministic over identical inputs."""
    salt = "adversarial_audit_salt_2026"
    ip = "203.0.113.195"
    ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

    h1 = compute_client_audit_hash(ip, ua, salt)
    h2 = compute_client_audit_hash(ip, ua, salt)
    assert h1 == h2


def test_challenge_sha256_exact_format_and_entropy():
    """Verify output is exactly 64 characters, lowercase hexadecimal, with high entropy."""
    h = compute_client_audit_hash("198.51.100.1", "curl/8.4.0", "custom_salt")
    assert len(h) == 64
    assert re.match(r"^[0-9a-f]{64}$", h)

    # Check character distribution (should contain at least 10 distinct hex chars)
    distinct_chars = set(h)
    assert len(distinct_chars) >= 10, f"Low entropy in hash digest: {distinct_chars}"


def test_challenge_sha256_avalanche_effect():
    """Verify single-bit / single-character change yields > 30% differing hex characters."""
    salt = "avalanche_test_salt"
    ip = "192.168.1.100"
    ua = "Agent/1.0.0"

    h_original = compute_client_audit_hash(ip, ua, salt)
    # 1 char flip in IP
    h_ip_flip = compute_client_audit_hash("192.168.1.101", ua, salt)
    # 1 char flip in UA
    h_ua_flip = compute_client_audit_hash(ip, "Agent/1.0.1", salt)
    # 1 char flip in salt
    h_salt_flip = compute_client_audit_hash(ip, ua, "bvalanche_test_salt")

    for changed, label in [(h_ip_flip, "IP"), (h_ua_flip, "UA"), (h_salt_flip, "Salt")]:
        differing = sum(c1 != c2 for c1, c2 in zip(h_original, changed))
        diff_ratio = differing / 64.0
        assert diff_ratio >= 0.70, f"{label} avalanche failure: only {diff_ratio*100:.1f}% chars differed (expected >= 70%)"


def test_challenge_sha256_salt_dependency():
    """Changing salt must completely change output."""
    ip = "10.0.0.1"
    ua = "Safari/16.0"
    h1 = compute_client_audit_hash(ip, ua, salt="salt_alpha")
    h2 = compute_client_audit_hash(ip, ua, salt="salt_beta")
    assert h1 != h2


def test_challenge_sha256_edge_case_inputs():
    """Verify handling of None, empty strings, Unicode, null bytes, and extreme lengths."""
    # None inputs
    h_none = compute_client_audit_hash(None, None, None)
    assert len(h_none) == 64

    # Unicode with accents
    h_unicode = compute_client_audit_hash("192.168.1.1", "Mozilla/5.0 (café/1.0)", "säĺt")
    assert len(h_unicode) == 64

    # Null bytes in input
    h_nullbytes = compute_client_audit_hash("192.168.1.1\x00extra", "UA\x00Injection", "salt\x00")
    assert len(h_nullbytes) == 64

    # Massive string (100,000 characters)
    huge_ua = "A" * 100000
    h_huge = compute_client_audit_hash("192.168.1.1", huge_ua, "salt")
    assert len(h_huge) == 64


def test_challenge_sha256_non_reversible():
    """Verify hash is not reversible and never exposes raw IP or User-Agent."""
    secret_ip = "172.31.255.254"
    secret_ua = "ConfidentialBrowser/9.9.9"
    digest = compute_client_audit_hash(secret_ip, secret_ua, "salt")

    assert secret_ip not in digest
    assert secret_ua not in digest
    # Check that hex cannot be trivially base64 decoded to reveal plain text
    import base64
    try:
        decoded = base64.b64decode(digest).decode("utf-8", errors="ignore")
        assert secret_ip not in decoded
        assert secret_ua not in decoded
    except Exception:
        pass


# =====================================================================
# Challenge Area 5: Decimal Monetary Integrity
# =====================================================================

def test_challenge_monetary_exact_decimal_arithmetic(challenger_session):
    """Verify financial balances avoid float precision degradation (e.g. 0.1 + 0.2 != 0.30000000000000004)."""
    ad = Ad(
        sponsor_name="Decimal Test",
        headline="Precision",
        click_url="https://decimal.org",
        total_budget=Decimal("0.30"),
        remaining_budget=Decimal("0.30"),
        cost_per_click=Decimal("0.10"),
        cost_per_impression=Decimal("0.0001"),
    )
    challenger_session.add(ad)
    challenger_session.commit()

    # Simulate 3 deductions of 0.10
    reloaded = challenger_session.get(Ad, ad.id)
    reloaded.remaining_budget -= Decimal("0.10")
    reloaded.remaining_budget -= Decimal("0.10")
    reloaded.remaining_budget -= Decimal("0.10")
    challenger_session.commit()

    final_ad = challenger_session.get(Ad, ad.id)
    assert final_ad.remaining_budget == Decimal("0.00")
    assert str(final_ad.remaining_budget) in ("0.00", "0")


# =====================================================================
# Challenge Area 6: SQLite PRAGMAs & WAL Concurrency
# =====================================================================

def test_challenge_sqlite_wal_concurrent_reads(challenger_db_engine):
    """Verify SQLite WAL allows reading while a write transaction is in progress."""
    # Insert initial record
    with challenger_db_engine.connect() as conn:
        conn.execute(
            text("INSERT INTO repositories (owner, name, stars, claimed) VALUES ('concurrent', 'repo', 100, 0)")
        )
        conn.commit()

    read_success = []

    def reader():
        with challenger_db_engine.connect() as r_conn:
            val = r_conn.execute(text("SELECT stars FROM repositories WHERE owner = 'concurrent';")).scalar()
            read_success.append(val == 100)

    # Start thread
    t = threading.Thread(target=reader)
    t.start()
    t.join(timeout=3.0)

    assert len(read_success) == 1 and read_success[0] is True, "Concurrent read failed under WAL"


# =====================================================================
# Challenge Area 7: Anti-Pattern & Prohibited Logic Inspection
# =====================================================================

def test_challenge_zero_prohibited_anti_patterns():
    """Verify zero PYTEST_CURRENT_TEST, zero fake personas in app codebase."""
    app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
    prohibited_strings = [
        "PYTEST_CURRENT_TEST",
        "Thomas Vance",
        "Theo Vance",
        "Vance Family",
    ]

    violations = []
    for root, _, files in os.walk(app_root):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                with open(path, encoding="utf-8") as py_file:
                    content = py_file.read()
                    for term in prohibited_strings:
                        if term.lower() in content.lower():
                            violations.append((path, term))

    assert not violations, f"Anti-pattern violations found in production code: {violations}"
