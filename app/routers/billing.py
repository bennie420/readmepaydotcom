"""
Billing & Payouts API Router (app/routers/billing.py).

Provides endpoints for:
- Advertiser deposit via PayPal Checkout.
- Advertiser deposit via Crypto (USDC / Multi-currency).
- Crypto IPN webhook listener.
- Maintainer Payout batch generation (PayPal MassPay & Crypto wallet disbursements).
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.payment_service import (
    capture_paypal_order,
    create_crypto_invoice,
    create_paypal_order,
    format_paypal_masspay_payload,
    generate_maintainer_payout_batches,
    process_crypto_payment_confirmation,
    process_paypal_webhook_event,
    verify_crypto_webhook_signature,
    verify_paypal_webhook_signature,
)


logger = logging.getLogger("router_billing")

router = APIRouter(prefix="/api/billing", tags=["Billing & Payments"])


# Request / Response Schemas
class PayPalOrderRequest(BaseModel):
    ad_id: int = Field(..., ge=1, description="Ad Campaign ID to fund")
    amount: Decimal = Field(..., gt=Decimal("0.00"), description="Deposit amount in USD")
    currency: str = Field(default="USD", description="Currency code (USD, EUR, GBP)")
    return_url: str | None = Field(default=None, description="Advertiser redirect on approval")
    cancel_url: str | None = Field(default=None, description="Advertiser redirect on cancel")


class PayPalCaptureRequest(BaseModel):
    order_id: str = Field(..., min_length=1, description="PayPal Order ID returned by checkout")


class CryptoInvoiceRequest(BaseModel):
    ad_id: int = Field(..., ge=1, description="Ad Campaign ID to fund")
    amount_usd: Decimal = Field(..., gt=Decimal("0.00"), description="Deposit amount in USD")
    pay_currency: str = Field(default="usdc", description="Cryptocurrency to pay with (usdc, usdt, btc, eth, sol)")
    description: str | None = Field(default=None, description="Optional invoice memo")


class CryptoConfirmRequest(BaseModel):
    ad_id: int = Field(..., ge=1, description="Ad Campaign ID to fund")
    amount_usd: Decimal = Field(..., gt=Decimal("0.00"), description="Amount in USD to credit")
    payment_id: str = Field(..., min_length=1, description="Transaction hash or payment ID")


# ==============================================================================
# PayPal Endpoints
# ==============================================================================


@router.post("/paypal/create-order", summary="Initiate PayPal checkout order for ad budget")
async def create_order_endpoint(payload: PayPalOrderRequest):
    """Create a PayPal checkout order for depositing advertiser budget."""
    try:
        order = await create_paypal_order(
            amount=payload.amount,
            ad_id=payload.ad_id,
            currency=payload.currency,
            return_url=payload.return_url,
            cancel_url=payload.cancel_url,
        )
        return order
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("PayPal order creation error: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"PayPal order creation failed: {str(e)}",
        )


@router.post("/paypal/capture-order", summary="Capture PayPal payment and credit ad campaign budget")
async def capture_order_endpoint(payload: PayPalCaptureRequest, db: Session = Depends(get_db)):
    """Capture approved PayPal checkout payment and credit campaign balance."""
    try:
        result = await capture_paypal_order(order_id=payload.order_id, db=db)
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error("PayPal capture error: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"PayPal capture failed: {str(e)}",
        )


@router.post("/paypal/webhook", summary="PayPal Webhook event listener")
async def paypal_webhook_endpoint(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Process incoming PayPal Webhooks for completed captures, approved orders, and payout disbursements.
    """
    try:
        body_json = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body")

    headers_dict = {k.lower(): v for k, v in request.headers.items()}
    is_valid = await verify_paypal_webhook_signature(headers=headers_dict, body_json=body_json)
    if not is_valid:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PayPal webhook signature verification failed")

    result = await process_paypal_webhook_event(event_data=body_json, db=db)
    return result



# ==============================================================================
# Crypto Endpoints
# ==============================================================================


@router.post("/crypto/create-invoice", summary="Create Crypto (USDC/SOL/BTC) payment invoice")
async def create_crypto_invoice_endpoint(payload: CryptoInvoiceRequest):
    """Generate crypto invoice details (address, amount, reference) for funding an ad campaign."""
    try:
        invoice = await create_crypto_invoice(
            amount_usd=payload.amount_usd,
            ad_id=payload.ad_id,
            pay_currency=payload.pay_currency,
            order_description=payload.description,
        )
        return invoice
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error("Crypto invoice error: %s", str(e))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post("/crypto/confirm", summary="Admin/Gateway confirmation of received crypto payment")
def confirm_crypto_payment_endpoint(payload: CryptoConfirmRequest, db: Session = Depends(get_db)):
    """Directly credit an ad campaign when a crypto transaction is confirmed on-chain."""
    try:
        result = process_crypto_payment_confirmation(
            ad_id=payload.ad_id,
            amount_usd=payload.amount_usd,
            payment_id=payload.payment_id,
            db=db,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post("/crypto/webhook", summary="Crypto IPN Webhook callback listener")
async def crypto_webhook_endpoint(
    request: Request,
    x_nowpayments_sig: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Process incoming IPN callbacks from crypto payment gateway."""
    body_bytes = await request.body()
    if not verify_crypto_webhook_signature(body_bytes, x_nowpayments_sig):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid HMAC signature")

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload")

    payment_status = data.get("payment_status")
    order_id = data.get("order_id", "")  # e.g., "ad_12"
    price_amount = data.get("price_amount")

    if payment_status in ("finished", "confirmed") and order_id.startswith("ad_") and price_amount:
        try:
            ad_id = int(order_id[3:])
            amount = Decimal(str(price_amount))
            payment_id = str(data.get("payment_id", data.get("invoice_id", "ipn_tx")))
            return process_crypto_payment_confirmation(
                ad_id=ad_id,
                amount_usd=amount,
                payment_id=payment_id,
                db=db,
            )
        except Exception as e:
            logger.error("Failed to process confirmed crypto IPN: %s", str(e))
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    return {"status": "ignored", "reason": f"Payment status {payment_status} not actionable"}


# ==============================================================================
# Maintainer Payout Ledger & Export Endpoints
# ==============================================================================


@router.get("/payouts/summary", summary="Inspect aggregate maintainer payouts grouped by PayPal vs Crypto")
def get_payouts_summary(db: Session = Depends(get_db)):
    """Calculate and return full payout distribution across all claimed repositories."""
    return generate_maintainer_payout_batches(db=db)


@router.get("/payouts/export-paypal", summary="Download PayPal Payouts MassPay batch JSON payload")
def export_paypal_batch(db: Session = Depends(get_db)):
    """Generate PayPal REST Payouts batch schema ready for submission to PayPal."""
    batches = generate_maintainer_payout_batches(db=db)
    return format_paypal_masspay_payload(batches["paypal_batch"])


@router.get("/payouts/export-crypto", summary="Download Crypto Payout batch instructions (USDC/SOL/ETH)")
def export_crypto_batch(db: Session = Depends(get_db)):
    """Generate crypto disbursements batch for bulk multisig / treasury execution."""
    batches = generate_maintainer_payout_batches(db=db)
    return {
        "network_currency": "USDC",
        "total_recipients": len(batches["crypto_batch"]),
        "total_disbursement_usd": batches["summary"]["total_crypto_usd"],
        "transfers": [
            {
                "recipient_address": item["payout_address"],
                "amount": item["amount_usd"],
                "memo": f"ReadmePay share for @{item['maintainer_handle']}",
            }
            for item in batches["crypto_batch"]
        ],
    }


@router.post("/payouts/run-automated", summary="Trigger automated maintainer payouts (PayPal & Crypto)")
async def run_automated_payouts_endpoint(
    request: Request,
    min_threshold_usd: float = 1.00,
    x_admin_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """
    Automated payout execution:
    Executes live PayPal Payouts API calls for PayPal maintainers and prepares Crypto batch disbursements.
    Guarded by X-Admin-Key authorization to protect against unauthorized triggers.
    """
    from app.config import settings
    expected_key = getattr(settings, "ADMIN_SECRET_KEY", None)
    if expected_key and x_admin_key != expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Valid X-Admin-Key header required to execute financial payouts.",
        )

    from app.services.payment_service import execute_automated_payouts
    res = await execute_automated_payouts(db=db, min_threshold_usd=Decimal(str(min_threshold_usd)))
    return res

