import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from astratrade.domain import (
    OrderIntent,
    OrderState,
    RiskDecision,
    Signal,
    Strategy,
    User,
)
from astratrade.repository import Repository


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository.in_memory()
        self.repo.save_user(User("user-1", "user@example.com", risk_confirmed=True))
        self.repo.save_strategy(
            Strategy("breakout", "1.0.0", "published", frozenset({"ETH-USDT-SWAP"}))
        )
        now = datetime.now(timezone.utc)
        self.signal = Signal(
            signal_id="signal-1",
            strategy_id="breakout",
            strategy_version="1.0.0",
            instrument="ETH-USDT-SWAP",
            side="long",
            entry_price=Decimal("2500"),
            stop_loss=Decimal("2490"),
            quantity=Decimal("0.1"),
            created_at=now,
            expires_at=now + timedelta(minutes=1),
        )
        self.repo.save_signal(self.signal)
        self.decision = RiskDecision(
            allowed=True,
            estimated_risk=Decimal("1"),
            estimated_notional=Decimal("250"),
        )

    def tearDown(self):
        self.repo.close()

    def make_intent(self):
        return OrderIntent(
            client_order_id="user-1:signal-1",
            signal_id="signal-1",
            user_id="user-1",
            instrument="ETH-USDT-SWAP",
            side="long",
            quantity=Decimal("0.1"),
            price=Decimal("2500"),
            builder_code="builder-test",
        )

    def test_user_strategy_and_order_round_trip(self):
        created = self.repo.create_order(self.make_intent(), self.decision)
        self.assertEqual(created.state, OrderState.INTENT)
        self.assertEqual(self.repo.get_user("user-1").email, "user@example.com")

        self.repo.save_risk_decision(self.signal.signal_id, self.decision)
        self.repo.audit("risk_approved", created.intent.client_order_id, {"allowed": True})
        updated = self.repo.advance_order(
            created.intent.client_order_id,
            OrderState.RISK_APPROVED,
        )
        updated = self.repo.advance_order(
            updated.intent.client_order_id,
            OrderState.SUBMITTED,
            exchange_order_id="okx-1",
        )

        self.assertEqual(updated.state, OrderState.SUBMITTED)
        self.assertEqual(updated.exchange_order_id, "okx-1")
        self.assertEqual(
            updated.history,
            [OrderState.INTENT, OrderState.RISK_APPROVED, OrderState.SUBMITTED],
        )
        self.assertEqual(self.repo.audit_count(created.intent.client_order_id), 1)

    def test_same_client_order_id_is_idempotent(self):
        first = self.repo.create_order(self.make_intent(), self.decision)
        second = self.repo.create_order(self.make_intent(), self.decision)

        self.assertEqual(first.intent, second.intent)
        self.assertEqual(second.history, [OrderState.INTENT])

    def test_denied_decision_cannot_create_order(self):
        denied = RiskDecision(allowed=False, reasons=("ORDER_RISK_LIMIT_EXCEEDED",))
        with self.assertRaises(ValueError):
            self.repo.create_order(self.make_intent(), denied)

    def test_unknown_user_is_rejected(self):
        intent = self.make_intent()
        intent = OrderIntent(
            client_order_id="unknown:signal-1",
            signal_id=intent.signal_id,
            user_id="unknown",
            instrument=intent.instrument,
            side=intent.side,
            quantity=intent.quantity,
            price=intent.price,
            builder_code=intent.builder_code,
        )
        with self.assertRaises(ValueError):
            self.repo.create_order(intent, self.decision)


if __name__ == "__main__":
    unittest.main()
