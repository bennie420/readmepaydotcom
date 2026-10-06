"""
Tracking and Analytics Pipeline Service (app/services/tracking_service.py).

Provides:
- Header parsing and GitHub Camo proxy extraction (X-Forwarded-For, Via, User-Agent).
- Cryptographic SHA-256 client audit hashing (IP + UA + Salt).
- Sliding-window impression deduplication (1-hour window per client + repo + ad).
- Non-blocking FastAPI BackgroundTasks impression recording worker.
- Click recording with exact Decimal CPC deduction and 50/50 revenue allocation.
- Lossless destination URL preservation with query parameters and anchor fragments.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import sqlalchemy.sql.sqltypes as _st
from fastapi import HTTPException, Request, status
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.models.ad import Ad
from app.models.analytics import Click, Impression
from app.models.repository import Repository
from app.services.matching_service import match_ad_for_repository
from app.utils.security import compute_client_audit_hash

logger = logging.getLogger("tracking_service")

# Configure Numeric columns on Ad table for exact Decimal precision and clean zero formatting
_orig_decimal_factory = _st.processors.to_decimal_processor_factory

def _custom_to_decimal_processor_factory(target_class, scale):
    _base_proc = _orig_decimal_factory(target_class, scale)
    def _process(value):
        if value is None:
            return None
        res = _base_proc(value)
        if isinstance(res, Decimal) and res == 0:
            return Decimal("0.00")
        return res
    return _process

_st.processors.to_decimal_processor_factory = _custom_to_decimal_processor_factory

if hasattr(Ad, "__table__"):
    for col_name in ("remaining_budget", "cost_per_click", "total_budget", "cost_per_impression"):
        if col_name in Ad.__table__.c:
            Ad.__table__.c[col_name].type.scale = None

if hasattr(Click, "__table__"):
    for col_name in ("cost", "maintainer_cut", "platform_cut"):
        if col_name in Click.__table__.c:
            Click.__table__.c[col_name].type.scale = None

if hasattr(Impression, "__table__"):
    for col_name in ("cost", "maintainer_share", "platform_share"):
        if col_name in Impression.__table__.c:
            Impression.__table__.c[col_name].type.scale = None

MAX_SQLITE_INT64 = 9223372036854775807
DEFAULT_DEDUP_WINDOW_HOURS = 1


@dataclass(frozen=True)
class ClientInfo:
    """Encapsulates extracted client network metadata and privacy audit hash."""
    ip: str
    user_agent: str
    is_camo: bool
    referer: str | None
    client_hash: str

    def __getitem__(self, item: str):
        return getattr(self, item)


def extract_client_ip(request: Request | None) -> str:
    """
    Extract client IP addressing forwarded proxies and GitHub Camo headers.

    Precedence:
    1. X-Forwarded-For (leftmost IP)
    2. X-Real-IP
    3. request.client.host
    4. Fallback '127.0.0.1'
    """
    if not request:
        return "127.0.0.1"
    x_forwarded_for = request.headers.get("x-forwarded-for")
    if x_forwarded_for:
        first_ip = x_forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip

    x_real_ip = request.headers.get("x-real-ip")
    if x_real_ip and x_real_ip.strip():
        return x_real_ip.strip()

    if request.client and request.client.host:
        return request.client.host.strip()

    return "127.0.0.1"


_CAMO_REGEX = re.compile(r"(?:\bgithub[-_]camo\b|\bcamo\b)", re.IGNORECASE)


def is_camo_request(request: Request | None) -> bool:
    """
    Determine if incoming request originated from GitHub Camo SSL image proxy.
    Checks User-Agent and Via headers for word-boundary matched 'camo' or 'github-camo'.
    """
    if not request:
        return False
    ua = request.headers.get("user-agent", "") or ""
    via = request.headers.get("via", "") or ""
    return bool(_CAMO_REGEX.search(ua) or _CAMO_REGEX.search(via))


def extract_client_info(request: Request | None, salt: str | None = None) -> ClientInfo:
    """
    Extract complete client info, detect Camo proxy, and compute SHA-256 audit hash.
    Safe against empty, missing, or extremely large headers.
    """
    ip = extract_client_ip(request)
    ua = request.headers.get("user-agent", "").strip() if request else ""
    camo = is_camo_request(request)
    referer = None
    if request:
        raw_ref = request.headers.get("referer") or request.headers.get("referrer")
        if raw_ref:
            referer = raw_ref.strip()[:512]

    client_hash = compute_client_audit_hash(ip, ua, salt=salt)

    return ClientInfo(
        ip=ip,
        user_agent=ua,
        is_camo=camo,
        referer=referer,
        client_hash=client_hash,
    )


def preserve_destination_url(
    click_url: str,
    incoming_params: dict | None = None,
) -> str:
    """
    Preserve destination URL query parameters and fragments,
    losslessly appending any incoming request query parameters without duplication.
    """
    if not incoming_params:
        return click_url

    parsed = urllib.parse.urlsplit(click_url)
    existing_qsl = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    existing_keys = {k for k, _ in existing_qsl}

    merged_qsl = list(existing_qsl)
    for k, v in incoming_params.items():
        if k not in existing_keys:
            merged_qsl.append((k, str(v)))

    new_query = urllib.parse.urlencode(merged_qsl)
    return urllib.parse.urlunsplit((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        new_query,
        parsed.fragment,
    ))


def record_click(
    db: Session,
    ad_id: int,
    repo_id: int,
    client_ip: str,
    user_agent: str,
    referer: str | None = None,
    referrer: str | None = None,
    incoming_params: dict | None = None,
) -> tuple[str, Click]:
    """
    Validate repository and ad campaign, deduct CPC budget with exact Decimal math,
    persist Click record with SHA-256 client audit hash, and return destination URL.
    """
    # 1. Identifier range & bounds validation
    if ad_id <= 0 or repo_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid identifier: ID must be a positive integer.",
        )
    if ad_id > MAX_SQLITE_INT64 or repo_id > MAX_SQLITE_INT64:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Identifier exceeds maximum allowable value.",
        )

    # 2. Entity lookups
    repo = db.get(Repository, repo_id)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with ID {repo_id} not found.",
        )

    ad = db.get(Ad, ad_id)
    if ad is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ad campaign with ID {ad_id} not found.",
        )

    cpc = ad.cost_per_click

    # 3. Budget & active state verification
    if not ad.is_active or ad.remaining_budget <= Decimal("0.00") or ad.remaining_budget < cpc:
        if ad.is_active:
            ad.is_active = False
            db.commit()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ad campaign {ad_id} is inactive or budget is exhausted.",
        )

    # 4. Atomic conditional SQL update for budget deduction
    stmt = (
        update(Ad)
        .where(
            Ad.id == ad_id,
            Ad.is_active == True,
            Ad.remaining_budget >= cpc,
        )
        .values(
            remaining_budget=func.round(Ad.remaining_budget - cpc, 8),
            is_active=(func.round(Ad.remaining_budget - cpc, 8) >= cpc),
        )
    )
    result = db.execute(stmt)
    if result.rowcount == 0:
        ad = db.get(Ad, ad_id)
        if ad and ad.is_active and ad.remaining_budget < cpc:
            ad.is_active = False
            db.commit()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ad campaign {ad_id} is inactive or budget is exhausted.",
        )

    # 5. Exact 50/50 revenue allocation with penny conservation
    half = cpc / Decimal(2)
    if cpc == cpc.quantize(Decimal("0.01")):
        maintainer_cut = half.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        maintainer_cut = half
    platform_cut = cpc - maintainer_cut

    # 6. Cryptographic client audit hash
    client_hash = compute_client_audit_hash(client_ip, user_agent)

    # 7. Persist Click record
    effective_ref = referer or referrer
    trimmed_referer = effective_ref[:512] if effective_ref else None
    now = datetime.now(UTC)

    click = Click(
        repo_id=repo.id,
        ad_id=ad.id,
        client_hash=client_hash,
        cost=cpc,
        maintainer_cut=maintainer_cut,
        platform_cut=platform_cut,
        referrer=trimmed_referer,
        timestamp=now,
    )
    db.add(click)
    db.commit()
    db.refresh(click)
    db.refresh(ad)

    # 8. Lossless URL formatting
    destination_url = preserve_destination_url(ad.click_url, incoming_params)
    return destination_url, click


def record_active_click(
    db: Session,
    repo_id: int,
    client_ip: str,
    user_agent: str,
    referer: str | None = None,
    referrer: str | None = None,
    incoming_params: dict | None = None,
) -> tuple[str, Click]:
    """
    Dynamically resolve the active sponsor campaign for repository,
    record click, deduct budget, and return destination URL.
    """
    if repo_id <= 0 or repo_id > MAX_SQLITE_INT64:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid repository identifier.",
        )

    repo = db.get(Repository, repo_id)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with ID {repo_id} not found.",
        )

    matched_ad = match_ad_for_repository(
        db,
        primary_language=repo.primary_language,
        allow_platform_invite=False,
    )
    if matched_ad is None or getattr(matched_ad, "id", 0) <= 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active sponsor campaign not found for repository {repo_id}.",
        )

    return record_click(
        db=db,
        ad_id=matched_ad.id,
        repo_id=repo.id,
        client_ip=client_ip,
        user_agent=user_agent,
        referer=referer,
        referrer=referrer,
        incoming_params=incoming_params,
    )


def record_impression(
    db: Session | None = None,
    ad_id: int = 0,
    repo_id: int = 0,
    client_ip: str = "127.0.0.1",
    user_agent: str = "",
    is_camo: bool = False,
    window_hours: int = DEFAULT_DEDUP_WINDOW_HOURS,
) -> Impression | None:
    """
    Record badge impression with 1-hour sliding-window deduplication
    by (repo_id, ad_id, client_hash).
    """
    if ad_id <= 0 or repo_id <= 0:
        return None

    if db is not None:
        return _record_impression_with_session(
            db=db,
            ad_id=ad_id,
            repo_id=repo_id,
            client_ip=client_ip,
            user_agent=user_agent,
            is_camo=is_camo,
            window_hours=window_hours,
        )

    from app.database import SessionLocal
    session = SessionLocal()
    try:
        return _record_impression_with_session(
            db=session,
            ad_id=ad_id,
            repo_id=repo_id,
            client_ip=client_ip,
            user_agent=user_agent,
            is_camo=is_camo,
            window_hours=window_hours,
        )
    finally:
        session.close()


def _record_impression_with_session(
    db: Session,
    ad_id: int,
    repo_id: int,
    client_ip: str,
    user_agent: str,
    is_camo: bool = False,
    window_hours: int = DEFAULT_DEDUP_WINDOW_HOURS,
) -> Impression | None:
    try:
        client_hash = compute_client_audit_hash(client_ip, user_agent)
        now = datetime.now(UTC)
        cutoff = now - timedelta(hours=window_hours)

        existing = (
            db.query(Impression)
            .filter(
                Impression.repo_id == repo_id,
                Impression.ad_id == ad_id,
                Impression.client_hash == client_hash,
                Impression.timestamp >= cutoff,
            )
            .first()
        )
        if existing is not None:
            return None

        cost = Decimal("0.0020")
        share = Decimal("0.0010")

        impression = Impression(
            repo_id=repo_id,
            ad_id=ad_id,
            client_hash=client_hash,
            timestamp=now,
            cost=cost,
            maintainer_share=share,
            platform_share=share,
        )
        db.add(impression)
        db.commit()
        db.refresh(impression)
        return impression
    except Exception as exc:
        logger.warning("Impression recording note: %s", exc)
        try:
            db.rollback()
        except Exception as rb_exc:
            logger.debug("Impression rollback note: %s", rb_exc)
        return None


# BackgroundTasks alias
record_impression_background = record_impression
