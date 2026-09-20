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
        self.assertEqual(len(response.cookies), 2)
        self.csrf_token = next(cookie.split(";", 1)[0].split("=", 1)[1] for cookie in response.cookies if cookie.startswith("astra_csrf="))
        return "; ".join(cookie.split(";", 1)[0] for cookie in response.cookies)

    def test_register_login_cookie_and_isolation(self):
        cookie = self.register()
        self.assertEqual(self.api.handle(Request("GET", "/v1/auth/me", cookie=cookie)).status, 200)
        with patch.dict("os.environ", {"ASTRA_SIM_PRICE": "60000"}):
            started = self.api.handle(Request("POST", "/v1/agent/start", cookie=cookie, csrf_token=self.csrf_token))
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
        invalid = self.api.handle(Request("PUT", "/v1/agent", cookie=user_cookie, csrf_token=self.csrf_token, body={"budget_usdt": 501, "frequency": "daily"}))
        self.assertEqual(invalid.status, 400)
        with patch.dict("os.environ", {"ASTRA_SIM_PRICE": "60000"}):
            first = self.api.handle(Request("POST", "/v1/agent/start", cookie=user_cookie, csrf_token=self.csrf_token))
        self.assertEqual(first.body["run"]["status"], "filled")
        self.assertEqual(Decimal(first.body["run"]["fee_usdt"]), Decimal("0.10000000"))
        self.assertEqual(len(self.store.orders(user_id)), 1)

    def test_dashboard_returns_equity_history_and_explains_risk_block(self):
        cookie = self.register()
        user_id = self.api.handle(Request("GET", "/v1/auth/me", cookie=cookie)).body["user_id"]
        with patch.dict("os.environ", {"ASTRA_SIM_PRICE": "60000"}):
            started = self.api.handle(Request("POST", "/v1/agent/start", cookie=cookie, csrf_token=self.csrf_token))
        self.assertEqual(started.body["run"]["status"], "filled")
        self.store.connection.execute("UPDATE sim_accounts SET cash_usdt='0' WHERE user_id=?", (user_id,))
        self.store.connection.commit()
        with patch.dict("os.environ", {"ASTRA_SIM_PRICE": "60000"}):
            blocked = self.api.handle(Request("POST", "/v1/agent/start", cookie=cookie, csrf_token=self.csrf_token))
            dashboard = self.api.handle(Request("GET", "/v1/dashboard", cookie=cookie))
        self.assertEqual(blocked.body["run"]["status"], "risk_blocked")
        self.assertGreaterEqual(len(dashboard.body["equity_history"]), 2)
        self.assertEqual(dashboard.body["risk_events"][0]["payload"]["reason"], "insufficient_cash_or_invalid_price")

    def test_csrf_password_change_and_session_revocation(self):
        cookie = self.register()
        blocked = self.api.handle(Request("POST", "/v1/agent/stop", cookie=cookie))
        self.assertEqual(blocked.status, 403)
        changed = self.api.handle(Request("POST", "/v1/auth/password", cookie=cookie, csrf_token=self.csrf_token, body={"current_password": "correct horse battery staple!", "new_password": "new correct horse battery!"}))
        self.assertEqual(changed.status, 204)
        self.assertEqual(self.api.handle(Request("GET", "/v1/auth/me", cookie=cookie)).status, 401)
        login = self.api.handle(Request("POST", "/v1/auth/login", remote_addr="127.0.0.1", body={"email": "user@example.com", "password": "new correct horse battery!"}))
        self.assertEqual(login.status, 200)

    def test_existing_session_gets_csrf_cookie_on_identity_refresh(self):
        cookie = self.register()
        session_cookie = next(part for part in cookie.split("; ") if part.startswith("astra_session="))
        refreshed = self.api.handle(Request("GET", "/v1/auth/me", cookie=session_cookie))
        self.assertEqual(refreshed.status, 200)
        self.assertEqual(len(refreshed.cookies), 1)
        self.assertTrue(refreshed.cookies[0].startswith("astra_csrf="))

    def test_login_rate_limit_and_failure_audit(self):
        for _ in range(5):
            response = self.api.handle(Request("POST", "/v1/auth/login", remote_addr="10.0.0.8", body={"email": "missing@example.com", "password": "incorrect password"}))
            self.assertEqual(response.status, 401)
        limited = self.api.handle(Request("POST", "/v1/auth/login", remote_addr="10.0.0.8", body={"email": "missing@example.com", "password": "incorrect password"}))
        self.assertEqual(limited.status, 429)
        failures = self.store.connection.execute("SELECT COUNT(*) FROM console_audit WHERE event_type='login_failed'").fetchone()[0]
        self.assertEqual(failures, 5)


if __name__ == "__main__":
    unittest.main()
