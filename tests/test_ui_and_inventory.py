"""
Unit and Integration Tests for Web UI, Static Favicon, and Inventory Endpoints.
"""

from __future__ import annotations

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from tests.conftest import create_test_ad, create_test_repository


def test_root_endpoint_content_negotiation(client: TestClient):
    """Verify root endpoint returns HTML for browser Accept headers and JSON for API clients."""
    # 1. Browser HTML request
    html_resp = client.get("/", headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
    assert html_resp.status_code == 200
    assert "text/html" in html_resp.headers["content-type"]
    assert "ReadmePay" in html_resp.text
    assert "Live Badge Studio" in html_resp.text

    # 2. JSON API request
    json_resp = client.get("/", headers={"Accept": "application/json"})
    assert json_resp.status_code == 200
    assert "application/json" in json_resp.headers["content-type"]
    data = json_resp.json()
    assert "name" in data
    assert "badge_docs" in data


def test_dashboard_endpoint_serves_html(client: TestClient):
    """Verify /app and /dashboard serve web UI HTML."""
    for path in ["/app", "/dashboard"]:
        resp = client.get(path)
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "ReadmePay" in resp.text


def test_favicon_endpoint(client: TestClient):
    """Verify /favicon.ico returns valid SVG icon with 200 OK."""
    resp = client.get("/favicon.ico")
    assert resp.status_code == 200
    assert "image/svg+xml" in resp.headers["content-type"]
    assert "<svg" in resp.text


def test_inventory_repos_endpoint(client: TestClient, db_session: Session):
    """Verify /api/inventory/repos returns catalog of registered repositories."""
    repo = create_test_repository(db_session, owner="org-inv", name="pkg-inv", claimed=True, maintainer_handle="dev_inv")
    resp = client.get("/api/inventory/repos")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "repositories" in data
    assert any(r["owner"] == "org-inv" and r["name"] == "pkg-inv" for r in data["repositories"])


def test_inventory_ads_endpoint(client: TestClient, db_session: Session):
    """Verify /api/inventory/ads returns active sponsor campaigns."""
    ad = create_test_ad(db_session, sponsor_name="TestSponsorCorp", target_language="Python")
    resp = client.get("/api/inventory/ads")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "ads" in data
    assert any(a["sponsor_name"] == "TestSponsorCorp" for a in data["ads"])


def test_repository_preview_endpoint(client: TestClient, db_session: Session):
    """Verify /api/repos/{owner}/{repo}/preview returns full metadata and snippets."""
    repo = create_test_repository(db_session, owner="preview-org", name="preview-repo")
    resp = client.get(f"/api/repos/{repo.owner}/{repo.name}/preview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["owner"] == "preview-org"
    assert data["name"] == "preview-repo"
    assert "snippets" in data
    assert "badge_url" in data
    assert "click_url" in data
