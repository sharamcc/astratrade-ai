import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from astratrade.api import ApplicationAPI, Request
from astratrade.auth import SessionAuth
from astratrade.console_api import ConsoleAPI
from astratrade.console_store import ConsoleStore
from astratrade.domain import OAuthTokenSet, User
from astratrade.oauth import OAuthService
from astratrade.repository import Repository
from astratrade.simulation import run_due


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

    def test_phase4_product_endpoints_are_scoped_and_explain_execution(self):
        cookie = self.register()
        templates = self.api.handle(Request("GET", "/v1/agent/templates", cookie=cookie))
        self.assertEqual(templates.status, 200)
        self.assertEqual({item["template_id"] for item in templates.body["items"]}, {"small_trial", "conservative", "frequent_small"})
        saved = self.api.handle(Request("PUT", "/v1/agent", cookie=cookie, csrf_token=self.csrf_token, body={"budget_usdt": 20, "frequency": "weekly", "template_id": "small_trial"}))
        self.assertEqual(saved.body["budget_usdt"], "20")
        with patch.dict("os.environ", {"ASTRA_SIM_PRICE": "60000"}):
            started = self.api.handle(Request("POST", "/v1/agent/start", cookie=cookie, csrf_token=self.csrf_token))
        order_id = started.body["run"]["order_id"]
        detail = self.api.handle(Request("GET", f"/v1/orders/{order_id}", cookie=cookie))
        self.assertEqual(detail.body["execution_reason"], "Agent 按已保存的定投配置执行模拟订单")
        self.assertEqual(detail.body["risk_decision"], "passed")
        analytics = self.api.handle(Request("GET", "/v1/analytics", cookie=cookie))
        self.assertEqual(analytics.body["order_count"], 1)
        notifications = self.api.handle(Request("GET", "/v1/notifications", cookie=cookie))
        self.assertGreaterEqual(notifications.body["unread_count"], 2)
        notice_id = notifications.body["items"][0]["notification_id"]
        self.assertEqual(self.api.handle(Request("POST", "/v1/notifications/read", cookie=cookie, csrf_token=self.csrf_token, body={"notification_id": notice_id})).status, 204)
        export = self.api.handle(Request("GET", "/v1/export/orders", cookie=cookie))
        self.assertIn("order_id", export.body["content"])
        agent_export = self.api.handle(Request("GET", "/v1/export/agent", cookie=cookie))
        self.assertIn('"simulation": true', agent_export.body["content"])
        self.assertEqual(self.api.handle(Request("GET", "/v1/orders/other", cookie=cookie)).status, 404)

    def test_market_price_failure_skips_execution_and_notifies(self):
        cookie = self.register()
        with patch("astratrade.console_api.current_btc_price", side_effect=RuntimeError("provider unavailable")):
            started = self.api.handle(Request("POST", "/v1/agent/start", cookie=cookie, csrf_token=self.csrf_token))
        self.assertEqual(started.body["run"]["reason"], "stale_market_price")
        self.assertEqual(self.store.orders(started.body["agent"]["user_id"]), [])
        notifications = self.api.handle(Request("GET", "/v1/notifications", cookie=cookie))
        self.assertTrue(any(item["notification_type"] == "market_price_stale" for item in notifications.body["items"]))

    def test_strategy_v2_configuration_versions_and_scoped_routes(self):
        cookie = self.register()
        created = self.api.handle(Request("POST", "/v1/strategies", cookie=cookie, csrf_token=self.csrf_token, body={"strategy_type": "moving_average", "config": {"short_window": 5, "long_window": 20, "budget_usdt": "25"}}))
        self.assertEqual(created.status, 201)
        strategy_id = created.body["strategy_id"]
        self.assertEqual(created.body["current_version"], "v1")
        listed = self.api.handle(Request("GET", "/v1/strategies", cookie=cookie))
        self.assertIn(strategy_id, [item["strategy_id"] for item in listed.body["items"]])
        updated = self.api.handle(Request("PUT", f"/v1/strategies/{strategy_id}", cookie=cookie, csrf_token=self.csrf_token, body={"config": {"short_window": 8, "long_window": 30, "budget_usdt": "30"}}))
        self.assertEqual(updated.body["current_version"], "v2")
        with patch.dict("os.environ", {"ASTRA_SIM_PRICE": "60000"}):
            started = self.api.handle(Request("POST", f"/v1/strategies/{strategy_id}/start", cookie=cookie, csrf_token=self.csrf_token))
        self.assertEqual(started.body["strategy"]["status"], "running")
        self.assertEqual(started.body["run"]["status"], "skipped")
        self.assertEqual(len(self.store.strategy_runs(started.body["strategy"]["user_id"], strategy_id)), 1)
        blocked_update = self.api.handle(Request("PUT", f"/v1/strategies/{strategy_id}", cookie=cookie, csrf_token=self.csrf_token, body={"config": {"short_window": 5, "long_window": 20, "budget_usdt": "25"}}))
        self.assertEqual(blocked_update.status, 400)
        stopped = self.api.handle(Request("POST", f"/v1/strategies/{strategy_id}/stop", cookie=cookie, csrf_token=self.csrf_token))
        self.assertEqual(stopped.body["status"], "stopped")
        self.assertEqual(len(self.api.handle(Request("GET", f"/v1/strategies/{strategy_id}/signals", cookie=cookie)).body["items"]), 1)
        self.assertEqual(self.api.handle(Request("GET", f"/v1/strategies/not-owned", cookie=cookie)).status, 404)

    def test_strategy_start_fills_once_and_repeated_scheduled_key_is_idempotent(self):
        cookie = self.register()
        user_id = self.api.handle(Request("GET", "/v1/auth/me", cookie=cookie)).body["user_id"]
        created = self.api.handle(Request("POST", "/v1/strategies", cookie=cookie, csrf_token=self.csrf_token, body={"strategy_type": "dca", "config": {"budget_usdt": "25", "frequency": "daily"}}))
        strategy_id = created.body["strategy_id"]
        with patch.dict("os.environ", {"ASTRA_SIM_PRICE": "60000"}):
            started = self.api.handle(Request("POST", f"/v1/strategies/{strategy_id}/start", cookie=cookie, csrf_token=self.csrf_token))
        self.assertEqual(started.body["run"]["status"], "filled")
        self.assertEqual(len(self.store.orders(user_id)), 1)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM market_snapshots WHERE user_id=?", (user_id,)).fetchone()[0], 1)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM risk_decisions WHERE user_id=?", (user_id,)).fetchone()[0], 1)
        duplicate = self.store.run_strategy_once(user_id, strategy_id, Decimal("60000"), started.body["run"]["execution_key"].split(":", 3)[3])
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(len(self.store.orders(user_id)), 1)

    def test_strategy_risk_block_is_audited_and_notified(self):
        cookie = self.register()
        created = self.api.handle(Request("POST", "/v1/strategies", cookie=cookie, csrf_token=self.csrf_token, body={"strategy_type": "dca", "config": {"budget_usdt": "500", "frequency": "daily"}}))
        strategy_id = created.body["strategy_id"]
        user_id = created.body["user_id"]
        self.store.connection.execute("UPDATE sim_accounts SET cash_usdt='100' WHERE user_id=?", (user_id,))
        self.store.connection.commit()
        with patch.dict("os.environ", {"ASTRA_SIM_PRICE": "60000"}):
            started = self.api.handle(Request("POST", f"/v1/strategies/{strategy_id}/start", cookie=cookie, csrf_token=self.csrf_token))
        self.assertEqual(started.body["run"]["status"], "risk_blocked")
        self.assertEqual(self.store.orders(user_id), [])
        events = self.store.audit_events(user_id)
        self.assertTrue(any(event["event_type"] == "risk_blocked" for event in events))
        notices = self.store.notifications(user_id)
        self.assertTrue(any(item["notification_type"] == "risk_blocked" for item in notices))

    def test_strategy_performance_export_and_worker_due_execution(self):
        cookie = self.register()
        user_id = self.api.handle(Request("GET", "/v1/auth/me", cookie=cookie)).body["user_id"]
        created = self.api.handle(Request("POST", "/v1/strategies", cookie=cookie, csrf_token=self.csrf_token, body={"strategy_type": "dca", "config": {"budget_usdt": "25", "frequency": "daily"}}))
        strategy_id = created.body["strategy_id"]
        scheduled = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        self.store.connection.execute("UPDATE strategy_instances SET status='running', next_run_at=? WHERE strategy_id=?", (scheduled, strategy_id))
        self.store.connection.commit()
        self.assertEqual(run_due(self.store, Decimal("60000")), 1)
        performance = self.api.handle(Request("GET", f"/v1/strategies/{strategy_id}/performance", cookie=cookie))
        self.assertEqual(performance.body["filled_run_count"], 1)
        self.assertEqual(performance.body["equity_current_usdt"], "9999.97520000")
        self.assertEqual(performance.body["max_drawdown_usdt"], "0")
        export = self.api.handle(Request("GET", "/v1/export/strategies", cookie=cookie))
        self.assertEqual(export.status, 200)
        self.assertIn("signal_id", export.body["content"])
        self.assertIn(strategy_id, export.body["content"])
        self.assertEqual(self.api.handle(Request("GET", f"/v1/strategies/{strategy_id}/signals", cookie=cookie)).body["items"][0]["risk_allowed"], 1)

    def test_legacy_agent_is_migrated_and_strategy_can_copy_or_deactivate(self):
        cookie = self.register()
        strategies = self.api.handle(Request("GET", "/v1/strategies", cookie=cookie)).body["items"]
        legacy = next(item for item in strategies if item["strategy_id"].startswith("legacy_dca_"))
        self.assertEqual(legacy["strategy_type"], "dca")
        copied = self.api.handle(Request("POST", f"/v1/strategies/{legacy['strategy_id']}/copy", cookie=cookie, csrf_token=self.csrf_token))
        self.assertEqual(copied.status, 201)
        deactivated = self.api.handle(Request("POST", f"/v1/strategies/{copied.body['strategy_id']}/deactivate", cookie=cookie, csrf_token=self.csrf_token))
        self.assertEqual(deactivated.body["status"], "inactive")
        self.assertEqual(self.api.handle(Request("POST", f"/v1/strategies/{copied.body['strategy_id']}/start", cookie=cookie, csrf_token=self.csrf_token)).status, 400)

    def test_strategy_and_signal_reads_are_isolated_between_users(self):
        cookie_a = self.register("a@example.com")
        invite_b, _ = self.store.create_invite(self.admin["user_id"])
        registered_b = self.api.handle(Request("POST", "/v1/auth/register", body={"email": "b@example.com", "password": "correct horse battery staple!", "invite_code": invite_b}))
        cookie_b = "; ".join(cookie.split(";", 1)[0] for cookie in registered_b.cookies)
        csrf_b = next(cookie.split(";", 1)[0].split("=", 1)[1] for cookie in registered_b.cookies if cookie.startswith("astra_csrf="))
        created_b = self.api.handle(Request("POST", "/v1/strategies", cookie=cookie_b, csrf_token=csrf_b, body={"strategy_type": "dca", "config": {"budget_usdt": "20", "frequency": "daily"}}))
        strategy_b = created_b.body["strategy_id"]
        self.assertEqual(self.api.handle(Request("GET", f"/v1/strategies/{strategy_b}", cookie=cookie_a)).status, 404)
        self.assertEqual(self.api.handle(Request("GET", f"/v1/strategies/{strategy_b}/signals", cookie=cookie_a)).status, 404)
        self.assertEqual(self.api.handle(Request("GET", f"/v1/strategies/{strategy_b}/performance", cookie=cookie_a)).status, 404)

    def test_strategy_schema_migration_is_repeatable(self):
        cookie = self.register()
        user_id = self.api.handle(Request("GET", "/v1/auth/me", cookie=cookie)).body["user_id"]
        before = self.store.connection.execute("SELECT COUNT(*) FROM strategy_instances WHERE user_id=?", (user_id,)).fetchone()[0]
        ConsoleStore(self.store.connection)
        after = self.store.connection.execute("SELECT COUNT(*) FROM strategy_instances WHERE user_id=?", (user_id,)).fetchone()[0]
        self.assertEqual(before, 1)
        self.assertEqual(after, before)

    def test_portfolio_strategy_uses_independent_simulated_positions(self):
        cookie = self.register()
        user_id = self.api.handle(Request("GET", "/v1/auth/me", cookie=cookie)).body["user_id"]
        created = self.api.handle(Request("POST", "/v1/strategies", cookie=cookie, csrf_token=self.csrf_token, body={"strategy_type": "portfolio_dca", "config": {"budget_usdt": "100", "frequency": "daily", "allocations": [{"instrument": "BTC-USDT", "weight": "0.7"}, {"instrument": "ETH-USDT", "weight": "0.3"}], "market_prices": [{"instrument": "ETH-USDT", "price": "3000"}]}}))
        strategy_id = created.body["strategy_id"]
        with patch.dict("os.environ", {"ASTRA_SIM_PRICE": "60000"}), patch("astratrade.console_api.current_market_prices", return_value={"BTC-USDT": Decimal("60000"), "ETH-USDT": Decimal("3000")}):
            started = self.api.handle(Request("POST", f"/v1/strategies/{strategy_id}/start", cookie=cookie, csrf_token=self.csrf_token))
        self.assertEqual(started.body["run"]["status"], "filled")
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM sim_orders WHERE user_id=?", (user_id,)).fetchone()[0], 2)
        eth = self.store.connection.execute("SELECT quantity FROM sim_positions WHERE user_id=? AND instrument='ETH-USDT'", (user_id,)).fetchone()
        self.assertIsNotNone(eth)
        self.assertGreater(Decimal(eth["quantity"]), Decimal("0"))

    def test_admin_overview_is_admin_only(self):
        user_cookie = self.register()
        self.assertEqual(self.api.handle(Request("GET", "/v1/admin/overview", cookie=user_cookie)).status, 403)
        self.repository.save_user(User(self.admin["user_id"], self.admin["email"], risk_confirmed=True))
        login = self.api.handle(Request("POST", "/v1/auth/login", body={"email": "admin@example.com", "password": "correct horse battery staple!"}))
        admin_cookie = "; ".join(cookie.split(";", 1)[0] for cookie in login.cookies)
        self.assertEqual(self.api.handle(Request("GET", "/v1/admin/overview", cookie=admin_cookie)).status, 200)

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
