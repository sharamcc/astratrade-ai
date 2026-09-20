import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from astratrade.domain import AccountSnapshot, AgentStatus, RiskProfile, Signal
from astratrade.risk import evaluate_signal


NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def make_signal(**overrides):
    values = {
        "signal_id": "signal-1",
        "strategy_id": "breakout",
        "strategy_version": "1.0.0",
        "instrument": "ETH-USDT-SWAP",
        "side": "long",
        "entry_price": Decimal("2500"),
        "stop_loss": Decimal("2490"),
        "quantity": Decimal("0.1"),
        "created_at": NOW - timedelta(seconds=5),
        "expires_at": NOW + timedelta(seconds=30),
    }
    values.update(overrides)
    return Signal(**values)


def make_profile(**overrides):
    values = {
        "max_order_risk_pct": Decimal("1"),
        "max_position_notional": Decimal("1000"),
        "max_daily_loss": Decimal("100"),
        "max_leverage": Decimal("3"),
        "allowed_instruments": frozenset({"ETH-USDT-SWAP"}),
        "max_orders_per_day": 5,
    }
    values.update(overrides)
    return RiskProfile(**values)


def make_account(**overrides):
    values = {
        "equity": Decimal("1000"),
        "realized_pnl_today": Decimal("0"),
        "position_notional": Decimal("0"),
        "active_orders": 0,
        "leverage": Decimal("2"),
        "observed_at": NOW - timedelta(seconds=5),
        "authorization_valid": True,
    }
    values.update(overrides)
    return AccountSnapshot(**values)


class RiskDecisionTests(unittest.TestCase):
    def test_valid_signal_is_allowed(self):
        decision = evaluate_signal(
            make_signal(),
            make_profile(),
            make_account(),
            agent_status=AgentStatus.RUNNING,
            strategy_published=True,
            now=NOW,
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reasons, ())
        self.assertEqual(decision.estimated_risk, Decimal("1.0"))

    def test_paused_agent_is_denied(self):
        decision = evaluate_signal(
            make_signal(),
            make_profile(),
            make_account(),
            agent_status=AgentStatus.PAUSED,
            strategy_published=True,
            now=NOW,
        )

        self.assertFalse(decision.allowed)
        self.assertIn("AGENT_NOT_RUNNING", decision.reasons)

    def test_stale_account_is_denied(self):
        decision = evaluate_signal(
            make_signal(),
            make_profile(),
            make_account(observed_at=NOW - timedelta(seconds=31)),
            agent_status=AgentStatus.RUNNING,
            strategy_published=True,
            now=NOW,
        )

        self.assertFalse(decision.allowed)
        self.assertIn("ACCOUNT_DATA_STALE", decision.reasons)

    def test_duplicate_signal_is_denied(self):
        decision = evaluate_signal(
            make_signal(),
            make_profile(),
            make_account(),
            agent_status=AgentStatus.RUNNING,
            strategy_published=True,
            now=NOW,
            seen_signal_ids={"signal-1"},
        )

        self.assertFalse(decision.allowed)
        self.assertIn("DUPLICATE_SIGNAL", decision.reasons)

    def test_risk_limit_is_denied(self):
        decision = evaluate_signal(
            make_signal(quantity=Decimal("2")),
            make_profile(),
            make_account(),
            agent_status=AgentStatus.RUNNING,
            strategy_published=True,
            now=NOW,
        )

        self.assertFalse(decision.allowed)
        self.assertIn("ORDER_RISK_LIMIT_EXCEEDED", decision.reasons)

    def test_authorization_and_daily_loss_are_fail_closed(self):
        decision = evaluate_signal(
            make_signal(),
            make_profile(),
            make_account(
                authorization_valid=False,
                realized_pnl_today=Decimal("-100"),
            ),
            agent_status=AgentStatus.RUNNING,
            strategy_published=True,
            now=NOW,
        )

        self.assertFalse(decision.allowed)
        self.assertIn("AUTHORIZATION_INVALID", decision.reasons)
        self.assertIn("DAILY_LOSS_LIMIT_REACHED", decision.reasons)


if __name__ == "__main__":
    unittest.main()
