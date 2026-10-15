"""Personal access tokens (ADR 0006). A token is 32 random bytes shown once when it is issued;
storage keeps only its SHA-256 hash, the first characters for recognition, an expiry and a
revocation time. Checking a token is a hash lookup: nothing secret is ever read back."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from novera.security.users import User

PREFIX = "nvr_"  # recognisable in logs and secret scanners
DEFAULT_DAYS = 90


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class IssuedToken:
    token_id: str
    user_id: str
    token: str  # shown once, never stored
    expires_at: datetime


def issue(repo, user_id: str, days: int = DEFAULT_DAYS, label: str = "") -> IssuedToken:
    """A new token for an active user, stored hashed."""
    user = repo.load_user(user_id)
    if user is None or not user.active:
        raise KeyError(f"no active user {user_id}")
    if days <= 0:
        raise ValueError("a token must expire after a positive number of days")
    token = PREFIX + secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    token_id = "tok_" + secrets.token_hex(6)
    expires = now + timedelta(days=days)
    repo.save_token(
        {
            "token_id": token_id,
            "token_hash": token_hash(token),
            "token_prefix": token[: len(PREFIX) + 6],
            "user_id": user_id,
            "label": label,
            "created_at": now,
            "expires_at": expires,
            "revoked_at": None,
        }
    )
    return IssuedToken(token_id, user_id, token, expires)


def user_for(repo, token: str | None) -> User | None:
    """The active user a token belongs to, or None when it is missing, unknown, expired or
    revoked."""
    if not token:
        return None
    row = repo.load_token(token_hash(token.strip()))
    if row is None or row["revoked_at"] is not None:
        return None
    expires = row["expires_at"]
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires <= datetime.now(UTC):
        return None
    user = repo.load_user(row["user_id"])
    return user if user is not None and user.active else None
