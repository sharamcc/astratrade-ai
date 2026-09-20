"""Opaque bearer-session authentication for the application boundary.

This is a session primitive, not a login product. A future password/OIDC
flow may call ``issue`` after authenticating a user. Only a SHA-256 digest is
stored; the raw token is returned once to the caller and is never logged.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from .repository import Repository


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SessionAuth:
    def __init__(self, repository: Repository, default_ttl: timedelta = timedelta(hours=8)):
        self.repository = repository
        self.default_ttl = default_ttl

    def issue(self, user_id: str, ttl: Optional[timedelta] = None) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + (ttl or self.default_ttl)
        self.repository.create_session(_digest(token), user_id, expires_at)
        self.repository.audit("session_issued", user_id, {"expires_at": expires_at.isoformat()}, user_id)
        return token

    def resolve(self, authorization_header: Optional[str]) -> Optional[str]:
        if not authorization_header:
            return None
        scheme, separator, token = authorization_header.partition(" ")
        if scheme.lower() != "bearer" or not separator or not token or " " in token:
            return None
        return self.repository.get_active_session_user(_digest(token))

    def revoke(self, token: str) -> None:
        if not token:
            return
        self.repository.revoke_session(_digest(token))
