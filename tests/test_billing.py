"""
Tests for Billing, PayPal Checkout, Crypto Payments, and Maintainer Payout Batches.
"""

from decimal import Decimal
import hmac
import hashlib
import pytest
from starlette.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.ad import Ad
from app.models.repository import Repository
from app.models.analytics import Click
from app.services.payment_service import (
    classify_payout_address,
    format_paypal_masspay_payload,
    generate_maintainer_payout_batches,
    process_crypto_payment_confirmation,
    verify_crypto_webhook_signature,
)


def test_classify_payout_address():
    """Verify classification of payout addresses into paypal, crypto, or unconfigured."""
    # PayPal emails
    assert classify_payout_address("maintainer@example.com") == "paypal"
    assert classify_payout_address("dev.support@sub.domain.org") == "paypal"

    # EVM addresses (Ethereum, Polygon, Arbitrum USDC)
    assert classify_payout_address("0x71C8360f06B9511676D58971f496733B94348a73") == "crypto"
    assert classify_payout_address("0x0000000000000000000000000000000000000000") == "crypto"

    # Solana addresses
    assert classify_payout_address("7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU") == "crypto"

    # Unconfigured / Invalid
    assert classify_payout_address(None) == "unconfigured"
    assert classify_payout_address("") == "unconfigured"
    assert classify_payout_address("invalid-handle") == "unconfigured"


def test_crypto_direct_confirmation(db_session: Session):
    """Verify that confirming a crypto payment credits ad budget and reactivates ad."""
    ad = Ad(
        sponsor_name="Crypto DevTools",
        headline="Fast Rust Framework",
        call_to_action="Install",
        click_url="https://example.com/rust",
        target_language="Rust",
        total_budget=Decimal("0.00"),
        remaining_budget=Decimal("0.00"),
        is_active=False,
    )
    db_session.add(ad)
    db_session.commit()
    db_session.refresh(ad)

    res = process_crypto_payment_confirmation(
        ad_id=ad.id,
        amount_usd=Decimal("250.00"),
        payment_id="tx_sol_883921",
        db=db_session,
    )

    assert res["status"] == "CONFIRMED"
    assert res["amount_credited"] == 250.00
    assert res["is_active"] is True
    assert ad.remaining_budget == Decimal("250.00")
    assert ad.total_budget == Decimal("250.00")


def test_crypto_invoice_api(client: TestClient, db_session: Session):
    """Verify crypto invoice generation endpoint."""
    ad = Ad(
        sponsor_name="Solana Tools",
        headline="Solana Toolkit",
        click_url="https://example.com/solana",
        total_budget=Decimal("50.00"),
        remaining_budget=Decimal("50.00"),
    )
    db_session.add(ad)
    db_session.commit()

    resp = client.post(
        "/api/billing/crypto/create-invoice",
        json={"ad_id": ad.id, "amount_usd": 150.00, "pay_currency": "usdc"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ad_id"] == ad.id
    assert data["amount_usd"] == 150.0
    assert data["pay_currency"] == "USDC"
    assert "receiving_wallet" in data


def test_crypto_confirm_endpoint(client: TestClient, db_session: Session):
    """Verify POST /api/billing/crypto/confirm endpoint."""
    ad = Ad(
        sponsor_name="Postgres Pro",
        headline="Cloud DB",
        click_url="https://example.com/pg",
        total_budget=Decimal("10.00"),
        remaining_budget=Decimal("0.00"),
        is_active=False,
    )
    db_session.add(ad)
    db_session.commit()

    resp = client.post(
        "/api/billing/crypto/confirm",
        json={"ad_id": ad.id, "amount_usd": 500.00, "payment_id": "tx_eth_0xabc123"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "CONFIRMED"
    assert body["amount_credited"] == 500.00
    assert body["new_remaining_budget"] == 500.00


def test_paypal_order_validation(client: TestClient):
    """Verify input validation for PayPal order creation."""
    # Negative amount
    resp = client.post(
        "/api/billing/paypal/create-order",
        json={"ad_id": 1, "amount": -10.00, "currency": "USD"},
    )
    assert resp.status_code == 422

    # Zero amount
    resp = client.post(
        "/api/billing/paypal/create-order",
        json={"ad_id": 1, "amount": 0.00, "currency": "USD"},
    )
    assert resp.status_code == 422


def test_maintainer_payout_batches_generation(db_session: Session):
    """Verify categorization of maintainer revenue into PayPal vs Crypto batches."""
    # 1. Maintainer with PayPal
    repo_pp = Repository(
        owner="octocat",
        name="paypal-project",
        claimed=True,
        maintainer_handle="octocat",
        payout_address="octocat@github.com",
    )
    # 2. Maintainer with Crypto
    repo_crypto = Repository(
        owner="solana-labs",
        name="crypto-project",
        claimed=True,
        maintainer_handle="soldev",
        payout_address="0x71C8360f06B9511676D58971f496733B94348a73",
    )
    # 3. Maintainer with no payout address
    repo_unconf = Repository(
        owner="anon",
        name="mystery-project",
        claimed=True,
        maintainer_handle="anon",
        payout_address=None,
    )
    ad = Ad(
        sponsor_name="Big Sponsor",
        headline="Top DevTools",
        click_url="https://example.com",
        cost_per_click=Decimal("2.00"),
    )
    db_session.add_all([repo_pp, repo_crypto, repo_unconf, ad])
    db_session.commit()

    # Add clicks: $2.00 gross per click -> $1.00 maintainer share per click
    click1 = Click(ad_id=ad.id, repo_id=repo_pp.id, client_hash="hash_1", cost=Decimal("2.00"))
    click2 = Click(ad_id=ad.id, repo_id=repo_crypto.id, client_hash="hash_2", cost=Decimal("2.00"))
    click3 = Click(ad_id=ad.id, repo_id=repo_unconf.id, client_hash="hash_3", cost=Decimal("2.00"))
    db_session.add_all([click1, click2, click3])
    db_session.commit()

    batches = generate_maintainer_payout_batches(db=db_session)

    # Validate PayPal batch
    assert len(batches["paypal_batch"]) == 1
    assert batches["paypal_batch"][0]["maintainer_handle"] == "octocat"
    assert batches["paypal_batch"][0]["payout_address"] == "octocat@github.com"
    assert batches["paypal_batch"][0]["amount_usd"] == 1.0

    # Validate Crypto batch
    assert len(batches["crypto_batch"]) == 1
    assert batches["crypto_batch"][0]["maintainer_handle"] == "soldev"
    assert batches["crypto_batch"][0]["payout_address"] == "0x71C8360f06B9511676D58971f496733B94348a73"
    assert batches["crypto_batch"][0]["amount_usd"] == 1.0

    # Validate Unconfigured
    assert len(batches["unconfigured"]) == 1
    assert batches["unconfigured"][0]["maintainer_handle"] == "anon"

    # Validate PayPal MassPay JSON formatter
    masspay = format_paypal_masspay_payload(batches["paypal_batch"])
    assert "sender_batch_header" in masspay
    assert len(masspay["items"]) == 1
    assert masspay["items"][0]["receiver"] == "octocat@github.com"
    assert masspay["items"][0]["amount"]["value"] == "1.00"


def test_payout_endpoints(client: TestClient, db_session: Session):
    """Verify payout API routes."""
    # Summary
    resp = client.get("/api/billing/payouts/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data
    assert "paypal_batch" in data
    assert "crypto_batch" in data

    # Export PayPal
    resp_pp = client.get("/api/billing/payouts/export-paypal")
    assert resp_pp.status_code == 200
    assert "sender_batch_header" in resp_pp.json()

    # Export Crypto
    resp_cr = client.get("/api/billing/payouts/export-crypto")
    assert resp_cr.status_code == 200
    assert "transfers" in resp_cr.json()

    # Trigger Automated Payout Execution (Guarded by X-Admin-Key)
    from app.config import settings
    admin_key = getattr(settings, "ADMIN_SECRET_KEY", "os-admin-secret-key-prod-2026")
    resp_unauthorized = client.post("/api/billing/payouts/run-automated?min_threshold_usd=0.50")
    assert resp_unauthorized.status_code == 401

    resp_run = client.post(
        "/api/billing/payouts/run-automated?min_threshold_usd=0.50",
        headers={"X-Admin-Key": admin_key},
    )
    assert resp_run.status_code == 200
    data_run = resp_run.json()
    assert "total_disbursed_usd" in data_run
    assert "recipients_paid" in data_run


def test_paypal_webhook_capture_completed(client: TestClient, db_session: Session):
    """Verify PayPal PAYMENT.CAPTURE.COMPLETED webhook automatically credits ad campaign."""
    ad = Ad(
        sponsor_name="Webhook Test Sponsor",
        headline="Instant Cloud",
        click_url="https://example.com",
        total_budget=Decimal("0.00"),
        remaining_budget=Decimal("0.00"),
        is_active=False,
    )
    db_session.add(ad)
    db_session.commit()
    db_session.refresh(ad)

    payload = {
        "id": "WH-12345",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": "CAP-8899",
            "custom_id": str(ad.id),
            "amount": {"value": "200.00", "currency_code": "USD"},
            "status": "COMPLETED",
        },
    }

    resp = client.post("/api/billing/paypal/webhook", json=payload)
    assert resp.status_code == 200
    res_json = resp.json()
    assert res_json["status"] == "PROCESSED"
    assert res_json["ad_id"] == ad.id
    assert res_json["amount_credited"] == 200.00

    db_session.refresh(ad)
    assert ad.remaining_budget == Decimal("200.00")
    assert ad.is_active is True


