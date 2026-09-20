import unittest
from decimal import Decimal

from astratrade.strategies import GridStrategy, MarketSnapshot, MovingAverageStrategy, PortfolioDcaStrategy, StrategyContext, build_strategy


def context(prices=(Decimal("10"),), current=Decimal("10"), previous=None, allocations=()):
    return StrategyContext(
        user_id="user-a",
        strategy_id="strategy-1",
        strategy_version="v1",
        market=MarketSnapshot("BTC-USDT", current, "2026-01-01T00:00:00+00:00", "market-1"),
        prices=tuple(prices),
        previous_price=previous,
        allocations=tuple(allocations),
    )


class StrategyTests(unittest.TestCase):
    def test_moving_average_golden_cross_is_deterministic(self):
        strategy = MovingAverageStrategy({"short_window": 2, "long_window": 3, "budget_usdt": "20"})
        result = strategy.evaluate(context((Decimal("8"), Decimal("8"), Decimal("7"), Decimal("10"))))
        self.assertEqual(result[0].action, "buy")
        self.assertEqual(result[0].reason, "moving_average_golden_cross")

    def test_moving_average_needs_history(self):
        result = MovingAverageStrategy({"short_window": 2, "long_window": 3, "budget_usdt": "20"}).evaluate(context((Decimal("8"), Decimal("9"))))
        self.assertEqual(result[0].action, "skip")
        self.assertEqual(result[0].reason, "insufficient_price_history")

    def test_grid_buy_and_out_of_range(self):
        strategy = GridStrategy({"lower_price": "90", "upper_price": "110", "grid_count": 4, "budget_usdt": "25"})
        self.assertEqual(strategy.evaluate(context(current=Decimal("94"), previous=Decimal("101")))[0].action, "buy")
        self.assertEqual(strategy.evaluate(context(current=Decimal("120"), previous=Decimal("110")))[0].reason, "grid_out_of_range")

    def test_portfolio_weights_and_partial_market_failure(self):
        strategy = PortfolioDcaStrategy({"budget_usdt": "100", "allocations": [{"instrument": "BTC-USDT", "weight": "0.7"}, {"instrument": "ETH-USDT", "weight": "0.3"}]})
        result = strategy.evaluate(context(current=Decimal("60000"), allocations=({"instrument": "ETH-USDT", "price": "3000"},)))
        self.assertEqual([signal.action for signal in result], ["buy", "buy"])
        self.assertEqual(result[0].requested_notional, Decimal("70.00000000"))

    def test_invalid_weights_and_strategy_type_are_rejected(self):
        with self.assertRaises(ValueError):
            PortfolioDcaStrategy({"budget_usdt": "100", "allocations": [{"instrument": "BTC-USDT", "weight": "0.8"}]})
        with self.assertRaises(ValueError):
            build_strategy("unknown", {})


if __name__ == "__main__":
    unittest.main()
