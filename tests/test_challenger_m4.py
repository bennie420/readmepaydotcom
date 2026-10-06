"""
tests/test_challenger_m4.py - Empirical Adversarial Challenge Suite for Milestone 4.

Milestone 4: Maintainer Claiming and Snippet Generation.
Tests:
1. POST /maintainers/claim:
   - Claim unclaimed repo updates DB (claimed=True, maintainer_handle, payout_address, claimed_at).
   - Claim already claimed repo returns 409 Conflict and preserves original data.
   - Claim non-existent repo returns honest 404 (strictly zero-mock).
   - Malformed/empty handle rejected with 422.
   - Whitespace-only handle, empty owner, missing name/repo rejected with 422.
   - Case-insensitive owner/name resolution and whitespace stripping.
   - Payout address whitespace normalization to None.
   - Concurrency race test: atomic claim check-and-set prevents double claim.
   - Aliases /api/repos/claim and /api/maintainers/claim.
2. GET /maintainers/snippet/{owner}/{repo}:
   - Markdown format contains badge and click target.
   - HTML format valid structure.
   - RST format valid structure.
   - Click URL points to /click/active/{repo.id}.
   - Missing repo returns 404.
   - Case-insensitive lookup.
   - Reverse proxy forwarded headers (X-Forwarded-Proto, X-Forwarded-Host).
   - Alias /api/repos/{owner}/{repo}/snippet.
3. GET /maintainers/claim informational endpoint.
4. Adversarial security tests: SQL injection strings, unicode / emoji handles.
"""

import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app.database import Base, get_db, get_engine_with_pragmas
from app.main import app
from app.models.repository import Repository
from tests.conftest import create_test_repository

# =====================================================================
# Fixture: Isolated File-Backed Database for Concurrency Tests
# =====================================================================

@pytest.fixture(scope="function")
def file_db_client():
    """Provides a file-backed SQLite database with WAL mode for concurrency testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        temp_path = tf.name

    db_url = f"sqlite:///{temp_path}"
    engine = get_engine_with_pragmas(db_url)
    Base.metadata.create_all(bind=engine)
    SessionMaker = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = SessionMaker()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield client, SessionMaker, engine

    app.dependency_overrides.pop(get_db, None)
    engine.dispose()
    if os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except OSError:
            pass


# =====================================================================
# 1. POST /maintainers/claim Core Empirical Verification
# =====================================================================

def test_emp_claim_unclaimed_repo_updates_db(client: TestClient, db_session):
    """Verify claiming an unclaimed repo updates DB state completely and returns 200."""
    repo = create_test_repository(db_session, owner="acme-corp", name="widget-lib", claimed=False)
    assert repo.claimed is False
    assert repo.maintainer_handle is None
    assert repo.claimed_at is None

    payload = {
        "owner": "acme-corp",
        "name": "widget-lib",
        "maintainer_handle": "lead_maintainer",
        "payout_address": "0x1111222233334444",
    }
    resp = client.post("/maintainers/claim", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()

    # Verify response body structure
    assert body["claimed"] is True
    assert body["maintainer_handle"] == "lead_maintainer"
    assert body["claimed_by"] == "lead_maintainer"
    assert body["payout_address"] == "0x1111222233334444"
    assert body["id"] == repo.id
    assert body["owner"] == "acme-corp"
    assert body["name"] == "widget-lib"
    assert "snippets" in body
    assert f"/click/active/{repo.id}" in body["snippets"]["click_url"]

    # Verify DB persistence directly
    db_session.refresh(repo)
    assert repo.claimed is True
    assert repo.maintainer_handle == "lead_maintainer"
    assert repo.payout_address == "0x1111222233334444"
    assert repo.claimed_at is not None


def test_emp_claim_already_claimed_repo_returns_409(client: TestClient, db_session):
    """Verify attempting to claim an already claimed repo returns 409 and does not overwrite DB."""
    repo = create_test_repository(
        db_session,
        owner="acme-corp",
        name="locked-repo",
        claimed=True,
        maintainer_handle="original_maintainer",
        payout_address="0xORIGINAL",
    )
    original_claimed_at = repo.claimed_at

    payload = {
        "owner": "acme-corp",
        "name": "locked-repo",
        "maintainer_handle": "intruder_maintainer",
        "payout_address": "0xINTRUDER",
    }
    resp = client.post("/maintainers/claim", json=payload)
    assert resp.status_code == 409, f"Expected 409 Conflict, got {resp.status_code}: {resp.text}"
    assert "already been claimed" in resp.json()["detail"].lower()

    # Verify DB state was NOT modified
    db_session.refresh(repo)
    assert repo.maintainer_handle == "original_maintainer"
    assert repo.payout_address == "0xORIGINAL"


def test_emp_claim_nonexistent_repo_returns_404(client: TestClient):
    """Verify claiming a non-existent repo returns honest 404 without creating mock entries."""
    payload = {
        "owner": "definitely-not-an-owner-9999",
        "name": "definitely-not-a-repo-9999",
        "maintainer_handle": "phantom_dev",
    }
    resp = client.post("/maintainers/claim", json=payload)
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.parametrize("bad_handle", ["", "   ", "\t\n", None])
def test_emp_claim_invalid_handle_rejected_422(client: TestClient, db_session, bad_handle):
    """Verify empty or whitespace-only maintainer_handle is rejected with 422."""
    repo = create_test_repository(db_session, owner="valid-org", name="valid-repo", claimed=False)
    payload = {
        "owner": "valid-org",
        "name": "valid-repo",
        "maintainer_handle": bad_handle,
    }
    resp = client.post("/maintainers/claim", json=payload)
    assert resp.status_code in [400, 422], f"Expected 422/400 for bad handle {bad_handle!r}, got {resp.status_code}"


def test_emp_claim_missing_required_fields_422(client: TestClient):
    """Verify requests missing required schema fields are rejected with 422."""
    # Missing owner
    resp1 = client.post("/maintainers/claim", json={"name": "repo1", "maintainer_handle": "dev"})
    assert resp1.status_code == 422

    # Missing both name and repo
    resp2 = client.post("/maintainers/claim", json={"owner": "org1", "maintainer_handle": "dev"})
    assert resp2.status_code == 422

    # Missing maintainer_handle
    resp3 = client.post("/maintainers/claim", json={"owner": "org1", "name": "repo1"})
    assert resp3.status_code == 422

    # Empty body
    resp4 = client.post("/maintainers/claim", json={})
    assert resp4.status_code == 422


def test_emp_claim_case_insensitivity_and_whitespace_trimming(client: TestClient, db_session):
    """Verify owner and repo lookup is case-insensitive and trims whitespace properly."""
    repo = create_test_repository(db_session, owner="MixedCaseOrg", name="CamelCaseRepo", claimed=False)

    payload = {
        "owner": "  mixedcaseorg  ",
        "name": "  camelcaserepo  ",
        "maintainer_handle": "  trimmed_maintainer  ",
        "payout_address": "  0xTRIMMED  ",
    }
    resp = client.post("/maintainers/claim", json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    db_session.refresh(repo)
    assert repo.claimed is True
    assert repo.maintainer_handle == "trimmed_maintainer"
    assert repo.payout_address == "0xTRIMMED"


def test_emp_claim_payout_address_whitespace_becomes_none(client: TestClient, db_session):
    """Verify whitespace-only payout_address is normalized to None."""
    repo = create_test_repository(db_session, owner="payout-org", name="payout-repo", claimed=False)
    payload = {
        "owner": repo.owner,
        "name": repo.name,
        "maintainer_handle": "dev_no_payout",
        "payout_address": "     ",
    }
    resp = client.post("/maintainers/claim", json=payload)
    assert resp.status_code == 200
    db_session.refresh(repo)
    assert repo.payout_address is None


def test_emp_claim_aliases(client: TestClient, db_session):
    """Verify claim route aliases /api/repos/claim and /api/maintainers/claim work identically."""
    r1 = create_test_repository(db_session, owner="alias-owner1", name="alias-repo1", claimed=False)
    resp1 = client.post(
        "/api/repos/claim",
        json={"owner": r1.owner, "name": r1.name, "maintainer_handle": "dev1"},
    )
    assert resp1.status_code == 200
    db_session.refresh(r1)
    assert r1.claimed is True

    r2 = create_test_repository(db_session, owner="alias-owner2", name="alias-repo2", claimed=False)
    resp2 = client.post(
        "/api/maintainers/claim",
        json={"owner": r2.owner, "repo": r2.name, "maintainer_handle": "dev2"},
    )
    assert resp2.status_code == 200
    db_session.refresh(r2)
    assert r2.claimed is True


def test_emp_claim_concurrency_race_condition(file_db_client):
    """Empirically verify atomic check-and-set prevents race conditions when claiming."""
    client, SessionMaker, engine = file_db_client

    # Setup repo in file db
    db = SessionMaker()
    repo = Repository(
        owner="race-org",
        name="race-repo",
        stars=10,
        primary_language="Python",
        claimed=False,
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)
    repo_id = repo.id
    db.close()

    results = []

    def attempt_claim(handle: str):
        c = TestClient(app)
        return c.post(
            "/maintainers/claim",
            json={
                "owner": "race-org",
                "name": "race-repo",
                "maintainer_handle": handle,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(attempt_claim, "competitor_alpha")
        f2 = executor.submit(attempt_claim, "competitor_beta")
        r1 = f1.result()
        r2 = f2.result()

    status_codes = sorted([r1.status_code, r2.status_code])
    assert status_codes == [200, 409], f"Expected one 200 and one 409, got {status_codes}"

    # Verify winning state
    db = SessionMaker()
    final_repo = db.query(Repository).filter(Repository.id == repo_id).first()
    assert final_repo.claimed is True
    assert final_repo.maintainer_handle in ["competitor_alpha", "competitor_beta"]
    db.close()


# =====================================================================
# 2. GET /maintainers/snippet/{owner}/{repo} Empirical Verification
# =====================================================================

def test_emp_snippet_markdown_html_rst_structure(client: TestClient, db_session):
    """Verify snippet generator endpoint produces structurally sound Markdown, HTML, and RST."""
    repo = create_test_repository(db_session, owner="badge-org", name="awesome-tool")
    resp = client.get(f"/maintainers/snippet/{repo.owner}/{repo.name}")
    assert resp.status_code == 200
    data = resp.json()

    assert data["owner"] == "badge-org"
    assert data["name"] == "awesome-tool"

    badge_url = data["badge_url"]
    click_url = data["click_url"]
    assert badge_url.endswith("/badge/badge-org/awesome-tool.svg")
    assert click_url.endswith(f"/click/active/{repo.id}")

    # Markdown format: [![Sponsorship Badge](badge_url)](click_url)
    md = data["markdown"]
    assert md == f"[![Sponsorship Badge]({badge_url})]({click_url})"

    # HTML format: <a href="click_url"><img src="badge_url" alt="Sponsorship Badge" /></a>
    html = data["html"]
    assert html == f'<a href="{click_url}"><img src="{badge_url}" alt="Sponsorship Badge" /></a>'

    # RST format: .. image:: badge_url\n   :target: click_url\n   :alt: Sponsorship Badge
    rst = data["rst"]
    assert rst == f".. image:: {badge_url}\n   :target: {click_url}\n   :alt: Sponsorship Badge"


def test_emp_snippet_click_url_points_to_repo_id(client: TestClient, db_session):
    """Verify click_url strictly references /click/active/{repo.id} with the accurate repo ID."""
    repo1 = create_test_repository(db_session, owner="multi-org", name="tool-one")
    repo2 = create_test_repository(db_session, owner="multi-org", name="tool-two")

    resp1 = client.get(f"/maintainers/snippet/{repo1.owner}/{repo1.name}").json()
    resp2 = client.get(f"/maintainers/snippet/{repo2.owner}/{repo2.name}").json()

    assert resp1["click_url"].endswith(f"/click/active/{repo1.id}")
    assert resp2["click_url"].endswith(f"/click/active/{repo2.id}")
    assert resp1["click_url"] != resp2["click_url"]


def test_emp_snippet_case_insensitivity(client: TestClient, db_session):
    """Verify snippet lookup is case-insensitive for owner and repo name."""
    repo = create_test_repository(db_session, owner="CaseOrg", name="CaseRepo")

    resp_upper = client.get("/maintainers/snippet/CASEORG/CASEREPO")
    assert resp_upper.status_code == 200
    assert resp_upper.json()["owner"] == "CaseOrg"
    assert resp_upper.json()["name"] == "CaseRepo"

    resp_lower = client.get("/maintainers/snippet/caseorg/caserepo")
    assert resp_lower.status_code == 200
    assert resp_lower.json()["owner"] == "CaseOrg"


def test_emp_snippet_reverse_proxy_forwarded_headers(client: TestClient, db_session):
    """Verify X-Forwarded-Proto and X-Forwarded-Host are honored in snippet URL generation."""
    repo = create_test_repository(db_session, owner="proxy-org", name="proxy-repo")
    headers = {
        "x-forwarded-proto": "https",
        "x-forwarded-host": "sponsorship.production.io",
    }
    resp = client.get(f"/maintainers/snippet/{repo.owner}/{repo.name}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["badge_url"] == f"https://sponsorship.production.io/badge/{repo.owner}/{repo.name}.svg"
    assert data["click_url"] == f"https://sponsorship.production.io/click/active/{repo.id}"
    assert "https://sponsorship.production.io" in data["markdown"]
    assert "https://sponsorship.production.io" in data["html"]
    assert "https://sponsorship.production.io" in data["rst"]


def test_emp_snippet_missing_repo_returns_404(client: TestClient):
    """Verify requesting snippet for unindexed repo returns 404."""
    resp = client.get("/maintainers/snippet/unindexed-org/unindexed-repo")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_emp_snippet_whitespace_owner_returns_404(client: TestClient):
    """Verify whitespace owner or repo path parameter returns 404."""
    resp = client.get("/maintainers/snippet/%20/%20")
    assert resp.status_code == 404


def test_emp_snippet_alias_endpoint(client: TestClient, db_session):
    """Verify alias /api/repos/{owner}/{repo}/snippet returns identical data."""
    repo = create_test_repository(db_session, owner="alias-test", name="alias-pkg")
    resp_standard = client.get(f"/maintainers/snippet/{repo.owner}/{repo.name}").json()
    resp_alias = client.get(f"/api/repos/{repo.owner}/{repo.name}/snippet").json()

    assert resp_standard == resp_alias


# =====================================================================
# 3. GET /maintainers/claim Informational Endpoint
# =====================================================================

def test_emp_get_claim_informational_endpoint(client: TestClient, db_session):
    """Verify GET /maintainers/claim informational endpoint responds correctly."""
    repo = create_test_repository(db_session, owner="info-org", name="info-app", claimed=True)

    # General call without query params
    r1 = client.get("/maintainers/claim")
    assert r1.status_code == 200
    assert r1.json()["status"] == "ready"

    # Call with existing claimed repo query
    r2 = client.get(f"/maintainers/claim?repo={repo.owner}/{repo.name}")
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["found_in_registry"] is True
    assert d2["claimed"] is True

    # Call with non-existing repo query
    r3 = client.get("/maintainers/claim?repo=phantom-org/phantom-repo")
    assert r3.status_code == 200
    d3 = r3.json()
    assert d3["found_in_registry"] is False
    assert d3["claimed"] is False


# =====================================================================
# 4. Adversarial Edge Cases & Security Injections
# =====================================================================

def test_emp_adversarial_unicode_and_special_handle(client: TestClient, db_session):
    """Verify Unicode, emojis, and special characters in maintainer handle persist faithfully."""
    repo = create_test_repository(db_session, owner="unicode-org", name="unicode-repo", claimed=False)
    complex_handle = "@maintainer_🚀_张伟_müller"
    payload = {
        "owner": repo.owner,
        "name": repo.name,
        "maintainer_handle": complex_handle,
        "payout_address": "lnurl1dp68gurn8ghj7mr0vdsk273095k8x735v3exw",
    }
    resp = client.post("/maintainers/claim", json=payload)
    assert resp.status_code == 200

    db_session.refresh(repo)
    assert repo.maintainer_handle == complex_handle
    assert repo.payout_address == "lnurl1dp68gurn8ghj7mr0vdsk273095k8x735v3exw"


def test_emp_adversarial_sql_injection_attempts(client: TestClient, db_session):
    """Verify SQL injection strings do not crash database or compromise query safety."""
    repo = create_test_repository(db_session, owner="safe-org", name="safe-repo", claimed=False)

    # SQL injection payload in owner
    sql_payload = {
        "owner": "safe-org' OR '1'='1",
        "name": "safe-repo",
        "maintainer_handle": "attacker",
    }
    resp = client.post("/maintainers/claim", json=sql_payload)
    # Must fail safely with 404 Not Found (since sanitized parameterized query finds no match)
    assert resp.status_code == 404

    # Verify original repository is untouched
    db_session.refresh(repo)
    assert repo.claimed is False
