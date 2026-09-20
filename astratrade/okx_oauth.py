"""Configurable OKX OAuth client.

OKX provides OAuth Broker/Connect credentials and the final OAuth endpoints
after approval. They are intentionally configuration values rather than
guessed constants in this repository.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from .domain import OAuthTokenSet


@dataclass(frozen=True)
class OKXOAuthConfig:
    client_id: str
    client_secret: str
    authorization_endpoint: str
    token_endpoint: str
    provider: str = "okx"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "OKXOAuthConfig":
        values = environ if environ is not None else os.environ
        required = {
            "ASTRA_OKX_CLIENT_ID": values.get("ASTRA_OKX_CLIENT_ID"),
            "ASTRA_OKX_CLIENT_SECRET": values.get("ASTRA_OKX_CLIENT_SECRET"),
            "ASTRA_OKX_OAUTH_AUTHORIZE_URL": values.get("ASTRA_OKX_OAUTH_AUTHORIZE_URL"),
            "ASTRA_OKX_OAUTH_TOKEN_URL": values.get("ASTRA_OKX_OAUTH_TOKEN_URL"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError("missing OAuth configuration: %s" % ", ".join(missing))
        return cls(
            client_id=required["ASTRA_OKX_CLIENT_ID"],
            client_secret=required["ASTRA_OKX_CLIENT_SECRET"],
            authorization_endpoint=required["ASTRA_OKX_OAUTH_AUTHORIZE_URL"],
            token_endpoint=required["ASTRA_OKX_OAUTH_TOKEN_URL"],
            provider=values.get("ASTRA_OKX_OAUTH_PROVIDER", "okx"),
        )


class OKXOAuthClient:
    """Authorization-code OAuth client using only explicitly configured URLs."""

    def __init__(self, config: OKXOAuthConfig, timeout: float = 12.0):
        self.config = config
        self.timeout = timeout
        self.provider = config.provider

    def authorization_url(self, state: str, redirect_uri: str, scopes: set[str]) -> str:
        query = urllib.parse.urlencode(
            {
                "client_id": self.config.client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": " ".join(sorted(scopes)),
                "state": state,
            }
        )
        separator = "&" if "?" in self.config.authorization_endpoint else "?"
        return self.config.authorization_endpoint + separator + query

    def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokenSet:
        payload = {
            "grant_type": "authorization_code",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        data = self._post_json(payload)
        return self._token_set(data)

    def _post_json(self, payload: dict[str, str]) -> dict:
        request = urllib.request.Request(
            self.config.token_endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "AstraTrade-AI/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:  # noqa: BLE001
            raise RuntimeError("OAuth token exchange failed") from error

    def _token_set(self, data: Mapping[str, object]) -> OAuthTokenSet:
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in", 3600)
        if not isinstance(access_token, str) or not isinstance(refresh_token, str):
            raise RuntimeError("OAuth response did not contain required tokens")
        try:
            lifetime = int(expires_in)
        except (TypeError, ValueError) as error:
            raise RuntimeError("OAuth response contained invalid expires_in") from error
        raw_scope = data.get("scope", "")
        scopes = frozenset(str(raw_scope).split())
        return OAuthTokenSet(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=lifetime),
            scopes=scopes,
        )
