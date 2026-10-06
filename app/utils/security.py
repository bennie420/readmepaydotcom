"""Cryptographic client audit hashing and privacy-preserving security utilities."""

import hashlib

from app.config import settings


def compute_client_audit_hash(
    client_ip: str | None,
    user_agent: str | None,
    salt: str | None = None
) -> str:
    """
    Computes a deterministic, one-way cryptographic SHA-256 digest
    for auditability and sliding-window deduplication.

    Guarantees:
    - Never stores raw PII (IP address or unhashed client identifiers).
    - Employs authentic SHA-256 cryptographic one-way hashing (strictly zero Base64 masquerading).
    - Salted with application secret to defend against rainbow-table precomputation.
    - Yields an exact 64-character lowercase hexadecimal string.
    """
    effective_salt = salt if salt is not None else settings.SALT
    normalized_ip = (client_ip or "").strip()
    normalized_ua = (user_agent or "").strip()
    payload = f"{normalized_ip}|{normalized_ua}|{effective_salt}".encode()
    return hashlib.sha256(payload).hexdigest()
