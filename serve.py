#!/usr/bin/env python3
"""AstraTrade AI test/production HTTP entrypoint.

The test mode is intentionally explicit and uses a stub OAuth provider. It
does not connect to OKX or place trades. Production mode requires approved
OAuth configuration and a real token-protection key.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

from astratrade.api import ApplicationAPI
from astratrade.auth import SessionAuth
from astratrade.console_api import ConsoleAPI
from astratrade.console_store import ConsoleStore
from astratrade.domain import OAuthTokenSet, User
from astratrade.http_server import create_server
from astratrade.oauth import OAuthService
from astratrade.okx_oauth import OKXOAuthClient, OKXOAuthConfig
from astratrade.repository import Repository
from astratrade.token_protector import FernetTokenProtector


class TestOAuthClient:
    provider = "okx-test"

    def authorization_url(self, state: str, redirect_uri: str, scopes: set[str]) -> str:
        return "https://test.invalid/authorize?state=%s" % state

    def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokenSet:
        return OAuthTokenSet(
            access_token="test-access-token",
            refresh_token="test-refresh-token",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            scopes=frozenset({"read", "trade"}),
        )


class TestTokenProtector:
    def seal(self, plaintext: str) -> str:
        return "test-sealed:" + plaintext


def build_server():
    mode = os.environ.get("ASTRA_RUN_MODE", "production").lower()
    db_path = os.environ.get("ASTRA_DB_PATH", "/var/lib/astratrade-ai/app.sqlite3")
    callback_uri = os.environ.get("ASTRA_CALLBACK_REDIRECT_URI")
    version = os.environ.get("ASTRA_VERSION", "dev")
    if not callback_uri:
        raise RuntimeError("ASTRA_CALLBACK_REDIRECT_URI is required")

    repository = Repository(sqlite3.connect(db_path, check_same_thread=False, timeout=30))
    product = ConsoleStore(sqlite3.connect(db_path, check_same_thread=False, timeout=30))
    test_user_id = os.environ.get("ASTRA_TEST_USER_ID")
    if mode == "test":
        if test_user_id:
            test_user_email = os.environ.get("ASTRA_TEST_USER_EMAIL", "test-user@localhost")
            repository.save_user(User(test_user_id, test_user_email, risk_confirmed=True))
            product.ensure_legacy_user(test_user_id, test_user_email)
        client = TestOAuthClient()
        protector = TestTokenProtector()
    else:
        client = OKXOAuthClient(OKXOAuthConfig.from_env())
        key = os.environ.get("ASTRA_TOKEN_PROTECTION_KEY")
        if not key:
            raise RuntimeError("ASTRA_TOKEN_PROTECTION_KEY is required in production")
        protector = FernetTokenProtector(key.encode("ascii"))

    oauth = OAuthService(repository, client, protector)
    api = ApplicationAPI(
        oauth,
        callback_redirect_uri=callback_uri,
        version=version,
    )
    port = int(os.environ.get("ASTRA_PORT", "8080"))
    host = os.environ.get("ASTRA_HOST", "127.0.0.1")
    sessions = SessionAuth(repository)
    static_dir = os.environ.get("ASTRA_STATIC_DIR", os.path.join(os.path.dirname(__file__), "web", "dist"))
    return create_server(ConsoleAPI(product, api, sessions), sessions.resolve, host=host, port=port, static_dir=static_dir)


if __name__ == "__main__":
    server = build_server()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
