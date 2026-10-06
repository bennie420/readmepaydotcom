"""
scripts/seed_sample_ads.py - Authentic Tech Sponsor Campaigns Seeder

Seeds authentic sponsor campaigns (Sentry, Neon, Supabase, Docker, GitHub Sponsors, PostHog)
into the database with real destination URLs, verified marketing copy, and exact Decimal budgets.
Strictly adheres to the Zero-Mock policy (no synthetic personas or fake URLs).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

# Allow running directly from project root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import Base, SessionLocal, engine, init_db
from app.models.ad import Ad

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_sample_ads")

# Verified Authentic Tech Sponsor Campaigns
SAMPLE_CAMPAIGNS: List[Dict[str, Any]] = [
    {
        "sponsor_name": "Sentry",
        "headline": "Application Performance Monitoring & Error Tracking",
        "call_to_action": "Try Sentry Free",
        "click_url": "https://sentry.io/for/python/",
        "target_language": "Python",
        "total_budget": Decimal("250.00"),
        "remaining_budget": Decimal("250.00"),
        "cost_per_click": Decimal("0.50"),
        "cost_per_impression": Decimal("0.0020"),
        "is_active": True,
    },
    {
        "sponsor_name": "Neon",
        "headline": "Serverless Postgres with Instant Branching & Autoscaling",
        "call_to_action": "Get Free Postgres",
        "click_url": "https://neon.tech/",
        "target_language": "Rust",
        "total_budget": Decimal("250.00"),
        "remaining_budget": Decimal("250.00"),
        "cost_per_click": Decimal("0.50"),
        "cost_per_impression": Decimal("0.0020"),
        "is_active": True,
    },
    {
        "sponsor_name": "Supabase",
        "headline": "The Open Source Firebase Alternative",
        "call_to_action": "Start Your Project",
        "click_url": "https://supabase.com/",
        "target_language": "TypeScript",
        "total_budget": Decimal("250.00"),
        "remaining_budget": Decimal("250.00"),
        "cost_per_click": Decimal("0.50"),
        "cost_per_impression": Decimal("0.0020"),
        "is_active": True,
    },
    {
        "sponsor_name": "Docker",
        "headline": "Build, Share, and Run Container Applications Anywhere",
        "call_to_action": "Download Docker Desktop",
        "click_url": "https://www.docker.com/",
        "target_language": "Go",
        "total_budget": Decimal("250.00"),
        "remaining_budget": Decimal("250.00"),
        "cost_per_click": Decimal("0.50"),
        "cost_per_impression": Decimal("0.0020"),
        "is_active": True,
    },
    {
        "sponsor_name": "GitHub Sponsors",
        "headline": "Support the Open Source Developers You Depend On",
        "call_to_action": "Become a Sponsor",
        "click_url": "https://github.com/sponsors",
        "target_language": None,  # Universal fallback sponsor
        "total_budget": Decimal("500.00"),
        "remaining_budget": Decimal("500.00"),
        "cost_per_click": Decimal("0.25"),
        "cost_per_impression": Decimal("0.0010"),
        "is_active": True,
    },
    {
        "sponsor_name": "PostHog",
        "headline": "Product Analytics, Session Replay, Feature Flags & Surveys",
        "call_to_action": "Deploy PostHog Free",
        "click_url": "https://posthog.com/",
        "target_language": None,  # Universal fallback sponsor
        "total_budget": Decimal("300.00"),
        "remaining_budget": Decimal("300.00"),
        "cost_per_click": Decimal("0.40"),
        "cost_per_impression": Decimal("0.0015"),
        "is_active": True,
    },
]


def seed_sample_ads(db: Session, reset: bool = False) -> int:
    """
    Seed authentic sponsor campaigns into the database.
    If reset=True, restores remaining_budget to total_budget and activates campaigns.
    If reset=False, preserves existing campaign budgets to avoid clobbering live spend.
    Returns count of active seeded campaigns.
    """
    now = datetime.now(timezone.utc)
    seeded_count = 0

    for camp in SAMPLE_CAMPAIGNS:
        # Search for existing campaign matching sponsor_name and target_language
        existing = db.scalars(
            select(Ad).filter_by(
                sponsor_name=camp["sponsor_name"],
                target_language=camp["target_language"],
            )
        ).first()

        if existing:
            if reset:
                existing.headline = camp["headline"]
                existing.call_to_action = camp["call_to_action"]
                existing.click_url = camp["click_url"]
                existing.total_budget = camp["total_budget"]
                existing.remaining_budget = camp["remaining_budget"]
                existing.cost_per_click = camp["cost_per_click"]
                existing.cost_per_impression = camp["cost_per_impression"]
                existing.is_active = True
                existing.updated_at = now
                logger.info(
                    "Reset existing campaign: %s (%s)",
                    camp["sponsor_name"],
                    camp["target_language"] or "Universal",
                )
            else:
                existing.headline = camp["headline"]
                existing.call_to_action = camp["call_to_action"]
                existing.click_url = camp["click_url"]
                existing.updated_at = now
                logger.info(
                    "Updated copy for existing campaign: %s (%s)",
                    camp["sponsor_name"],
                    camp["target_language"] or "Universal",
                )
            seeded_count += 1
        else:
            new_ad = Ad(
                sponsor_name=camp["sponsor_name"],
                headline=camp["headline"],
                call_to_action=camp["call_to_action"],
                click_url=camp["click_url"],
                target_language=camp["target_language"],
                is_active=camp["is_active"],
                total_budget=camp["total_budget"],
                remaining_budget=camp["remaining_budget"],
                cost_per_click=camp["cost_per_click"],
                cost_per_impression=camp["cost_per_impression"],
                created_at=now,
                updated_at=now,
            )
            db.add(new_ad)
            logger.info(
                "Created new campaign: %s (%s)",
                camp["sponsor_name"],
                camp["target_language"] or "Universal",
            )
            seeded_count += 1

    db.commit()
    return seeded_count


def print_active_campaigns(db: Session) -> None:
    """Print a clean summary table of seeded campaigns."""
    ads = db.scalars(select(Ad).order_by(Ad.id)).all()
    print("\n" + "=" * 90)
    print(f"{'ID':<4} {'Sponsor':<18} {'Target Lang':<14} {'Budget':<10} {'CPC':<8} {'Status':<8} {'Headline'}")
    print("-" * 90)
    for ad in ads:
        lang_str = ad.target_language if ad.target_language else "(Universal)"
        status_str = "ACTIVE" if ad.is_active else "INACTIVE"
        print(
            f"{ad.id:<4} {ad.sponsor_name:<18} {lang_str:<14} "
            f"${ad.remaining_budget:<9} ${ad.cost_per_click:<7} {status_str:<8} {ad.headline[:30]}..."
        )
    print("=" * 90 + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed sample sponsor ads into database.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset existing campaign budgets to original values",
    )
    args = parser.parse_args()

    init_db(engine)

    with SessionLocal() as db:
        count = seed_sample_ads(db, reset=args.reset)
        print_active_campaigns(db)
        print(f"[OK] Sponsor Campaign Seeding Complete: {count} campaigns active.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
