import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from astratrade.api import ApplicationAPI, Request
from astratrade.auth import SessionAuth
from astratrade.console_api import ConsoleAPI
from astratrade.console_store import ConsoleStore
from astratrade.domain import OAuthTokenSet
from astratrade.oauth import OAuthService
from astratrade.repository import Repository


class OAuthClient:
    provider = "okx-test"

    def authorization_url(self, state, redirect_uri, scopes):
        return "https://test.invalid/authorize?state=" + state

    def exchange_code(self, code, redirect_uri):
        return OAuthTokenSet("access", "refresh", datetime.now(timezone.utc) + timedelta(hours=1), frozenset({"read"}))


class Protector:
    def seal(self, value):
        return "sealed:" + value


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.repository = Repository.in_memory()
        self.store = ConsoleStore(sqlite3.connect(":memory:"))
        oauth = OAuthService(self.repository, OAuthClient(), Protector())
        self.api = ConsoleAPI(self.store, ApplicationAPI(oauth, "https://test.invalid/callback"), SessionAuth(self.repository))
        self.admin = self.store.create_admin("admin@example.com", "correct horse battery staple!")
        self.invite, _ = self.store.create_invite(self.admin["user_id"])

    def tearDown(self):
        self.store.connection.close()
        self.repository.close()

    def register(self, email="user@example.com"):
        response = self.api.handle(Request("POST", "/v1/auth/register", body={"email": email, "password": "correct horse battery staple!", "invite_code": self.invite}))
        self.assertEqual(response.status, 201)
        return response.headers["Set-Cookie"].split(";", 1)[0]

    def test_register_login_cookie_and_isolation(self):
        cookie = self.register()
        self.assertEqual(self.api.handle(Request("GET", "/v1/auth/me", cookie=cookie)).status, 200)
        with patch.dict("os.environ", {"ASTRA_SIM_PRICE": "60000"}):
            started = self.api.handle(Request("POST", "/v1/agent/start", cookie=cookie))
        self.assertEqual(started.status, 200)
        self.assertEqual(len(self.store.orders(started.body["agent"]["user_id"])), 1)
        self.assertEqual(self.api.handle(Request("GET", "/v1/dashboard")).status, 401)

    def test_invite_is_single_use_and_password_is_verified(self):
        self.register()
        duplicate = self.api.handle(Request("POST", "/v1/auth/register", body={"email": "two@example.com", "password": "correct horse battery staple!", "invite_code": self.invite}))
        self.assertEqual(duplicate.status, 400)
        login = self.api.handle(Request("POST", "/v1/auth/login", body={"email": "user@example.com", "password": "bad password"}))
        self.assertEqual(login.status, 401)

    def test_budget_limit_and_idempotent_run(self):
        user_cookie = self.register()
        user_id = self.api.handle(Request("GET", "/v1/auth/me", cookie=user_cookie)).body["user_id"]
        invalid = self.api.handle(Request("PUT", "/v1/agent", cookie=user_cookie, body={"budget_usdt": 501, "frequency": "daily"}))
        self.assertEqual(invalid.status, 400)
        with patch.dict("os.environ", {"ASTRA_SIM_PRICE": "60000"}):
            first = self.api.handle(Request("POST", "/v1/agent/start", cookie=user_cookie))
        self.assertEqual(first.body["run"]["status"], "filled")
        self.assertEqual(Decimal(first.body["run"]["fee_usdt"]), Decimal("0.10000000"))
        self.assertEqual(len(self.store.orders(user_id)), 1)


if __name__ == "__main__":
    unittest.main()
