"""Provider-neutral OAuth boundary.

The real OKX client must be injected by the application layer. This module
owns state validation and persistence rules, not HTTP calls or client secrets.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import uuid4

from .domain import ConnectionStatus, OAuthConnection, OAuthTokenSet
from .repository import Repository


class OAuthClient(Protocol):
    provider: str

    def authorization_url(self, state: str, redirect_uri: str, scopes: set[str]) -> str:
        """Return a provider URL. The client must encode state verbatim."""

    def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokenSet:
        """Exchange a one-time authorization code for tokens."""


class TokenProtector(Protocol):
    def seal(self, plaintext: str) -> str:
        """Encrypt or envelope-encrypt a token using a managed key."""


@dataclass(frozen=True)
class OAuthStart:
    state: str
    authorization_url: str
    expires_at: datetime


class OAuthService:
    """Coordinates OAuth without exposing provider tokens to callers."""

    def __init__(
        self,
        repository: Repository,
        client: OAuthClient,
        protector: TokenProtector,
        state_ttl: timedelta = timedelta(minutes=10),
    ):
        self.repository = repository
        self.client = client
        self.protector = protector
        self.state_ttl = state_ttl

    def begin(self, user_id: str, redirect_uri: str, scopes: set[str]) -> OAuthStart:
        if not redirect_uri.startswith("https://"):
            raise ValueError("redirect URI must use HTTPS")
        if self.repository.get_user(user_id) is None:
            raise ValueError("unknown user")
        state = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + self.state_ttl
        self.repository.save_oauth_state(state, user_id, redirect_uri, expires_at)
        return OAuthStart(
            state=state,
            authorization_url=self.client.authorization_url(state, redirect_uri, scopes),
            expires_at=expires_at,
        )

    def complete(self, state: str, code: str, redirect_uri: str) -> OAuthConnection:
        pending = self.repository.consume_oauth_state(state)
        if pending is None:
            raise ValueError("invalid, expired, or already-used OAuth state")
        user_id, expected_redirect_uri = pending
        if redirect_uri != expected_redirect_uri:
            raise ValueError("redirect URI mismatch")
        if not code:
            raise ValueError("authorization code is required")

        tokens = self.client.exchange_code(code, redirect_uri)
        connection = OAuthConnection(
            connection_id=str(uuid4()),
            user_id=user_id,
            provider=self.client.provider,
            status=ConnectionStatus.ACTIVE,
            encrypted_access_token=self.protector.seal(tokens.access_token),
            encrypted_refresh_token=self.protector.seal(tokens.refresh_token),
            expires_at=tokens.expires_at,
            scopes=tokens.scopes,
        )
        self.repository.save_exchange_connection(connection)
        self.repository.audit(
            "oauth_connected",
            connection.connection_id,
            {"provider": connection.provider, "scopes": sorted(connection.scopes)},
            user_id=user_id,
        )
        return connection

    def revoke(self, user_id: str) -> None:
        connection = self.repository.get_exchange_connection(user_id)
        if connection is None:
            return
        self.repository.set_connection_status(user_id, ConnectionStatus.REVOKED)
        self.repository.audit(
            "oauth_revoked", connection.connection_id, {}, user_id=user_id
        )

    def has_valid_connection(self, user_id: str) -> bool:
        return self.repository.get_usable_exchange_connection(user_id) is not None
