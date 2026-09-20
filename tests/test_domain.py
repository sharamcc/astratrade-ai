import unittest
from decimal import Decimal

from astratrade.domain import OrderIntent, OrderRecord, OrderState


class OrderRecordTests(unittest.TestCase):
    def setUp(self):
        intent = OrderIntent(
            client_order_id="user-1:signal-1",
            signal_id="signal-1",
            user_id="user-1",
            instrument="ETH-USDT-SWAP",
            side="long",
            quantity=Decimal("0.01"),
            price=Decimal("2500"),
        )
        self.order = OrderRecord(intent)

    def test_valid_lifecycle_is_recorded(self):
        self.order.advance(OrderState.RISK_APPROVED)
        self.order.advance(OrderState.SUBMITTED)
        self.order.advance(OrderState.ACCEPTED, exchange_order_id="okx-1")
        self.order.advance(OrderState.FILLED)
        self.order.advance(OrderState.RECONCILED)

        self.assertEqual(self.order.state, OrderState.RECONCILED)
        self.assertEqual(self.order.exchange_order_id, "okx-1")
        self.assertEqual(
            self.order.history,
            [
                OrderState.INTENT,
                OrderState.RISK_APPROVED,
                OrderState.SUBMITTED,
                OrderState.ACCEPTED,
                OrderState.FILLED,
                OrderState.RECONCILED,
            ],
        )

    def test_invalid_transition_is_rejected(self):
        with self.assertRaises(ValueError):
            self.order.advance(OrderState.FILLED)

    def test_reconciled_order_cannot_change(self):
        self.order.advance(OrderState.REJECTED)
        with self.assertRaises(ValueError):
            self.order.advance(OrderState.RECONCILED)


if __name__ == "__main__":
    unittest.main()
