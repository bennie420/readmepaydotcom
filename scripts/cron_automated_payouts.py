#!/usr/bin/env python3
"""
Automated Maintainer Revenue Payout Cron Script.

Runs on a recurring schedule (e.g. 1st of every month or weekly) to:
1. Reconcile maintainer revenue across all claimed repositories.
2. Filter maintainers meeting minimum payout threshold.
3. Dispatch automated PayPal Payouts for maintainers with registered PayPal accounts.
4. Record Crypto batch settlements for maintainers with registered wallet addresses.
5. Log transaction IDs and audit history in SQLite database.

Usage:
    python scripts/cron_automated_payouts.py [--threshold 5.00] [--dry-run]
"""

import argparse
import asyncio
from decimal import Decimal
import os
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from app.database import SessionLocal, engine
from app.services.payment_service import execute_automated_payouts, generate_maintainer_payout_batches


async def run_payout_cron(min_threshold: Decimal, dry_run: bool = False):
    print("=" * 60)
    print("  OpenSponsor Automated Maintainer Payout Engine")
    print(f"  Minimum Threshold: ${min_threshold:.2f} USD | Dry Run: {dry_run}")
    print("=" * 60)

    db = SessionLocal()
    try:
        if dry_run:

            print("[INFO] Calculating pending maintainer payouts (Dry Run)...")
            batches = generate_maintainer_payout_batches(db)
            print(f"[SUMMARY] Total PayPal Maintainers: {batches['summary']['total_paypal_recipients']} (${batches['summary']['total_paypal_usd']:.2f})")
            print(f"[SUMMARY] Total Crypto Maintainers: {batches['summary']['total_crypto_recipients']} (${batches['summary']['total_crypto_usd']:.2f})")
            print(f"[SUMMARY] Total Unconfigured:        {batches['summary']['total_unconfigured_recipients']} (${batches['summary']['total_unconfigured_usd']:.2f})")
            print(f"[TOTAL]   Grand Total Pending:      ${batches['summary']['grand_total_pending_usd']:.2f}")
            return

        print("[INFO] Executing live maintainer payouts...")
        results = await execute_automated_payouts(db=db, min_threshold_usd=min_threshold)

        print(f"[OK] Total Disbursed: ${results['total_disbursed_usd']:.2f} across {results['recipients_paid']} maintainers.")
        if results.get("paypal_payout"):
            pp = results["paypal_payout"]
            print(f"  -> PayPal Batch: {pp['batch_reference']} (External ID: {pp['external_id']}, Status: {pp['status']})")
        if results.get("crypto_payout"):
            cr = results["crypto_payout"]
            print(f"  -> Crypto Batch: {cr['batch_reference']} (Status: {cr['status']})")
        print("=" * 60)
    finally:
        db.close()



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Automated Maintainer Revenue Payout Cron")
    parser.add_argument("--threshold", type=float, default=1.00, help="Minimum payout threshold in USD (default: $1.00)")
    parser.add_argument("--dry-run", action="store_true", help="Calculate without executing live transactions")
    args = parser.parse_args()

    asyncio.run(run_payout_cron(min_threshold=Decimal(str(args.threshold)), dry_run=args.dry_run))
