"""Deterministic, side-effect-free Strategy V2 signal generation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol


@dataclass(frozen=True)
class MarketSnapshot:
    instrument: str
    price: Decimal
    observed_at: str
    snapshot_id: str = ""


@dataclass(frozen=True)
class StrategyContext:
    user_id: str
    strategy_id: str
    strategy_version: str
    market: MarketSnapshot
    prices: tuple[Decimal, ...] = ()
    previous_price: Decimal | None = None
    allocations: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Signal:
    signal_id: str
    user_id: str
    strategy_id: str
    strategy_version: str
    instrument: str
    action: str
    requested_notional: Decimal
    reason: str
    market_snapshot_id: str


class Strategy(Protocol):
    strategy_type: str

    def validate_config(self) -> None: ...

    def evaluate(self, context: StrategyContext) -> list[Signal]: ...


def _decimal(config: dict[str, Any], key: str) -> Decimal:
    try:
        value = Decimal(str(config[key]))
    except (KeyError, InvalidOperation, ValueError) as error:
        raise ValueError(f"{key} must be a valid number") from error
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{key} must be greater than zero")
    return value


def _signal(context: StrategyContext, instrument: str, action: str, notional: Decimal, reason: str, suffix: str) -> Signal:
    return Signal(
        signal_id=f"{context.strategy_id}:{context.strategy_version}:{context.market.observed_at}:{instrument}:{suffix}",
        user_id=context.user_id,
        strategy_id=context.strategy_id,
        strategy_version=context.strategy_version,
        instrument=instrument,
        action=action,
        requested_notional=notional,
        reason=reason,
        market_snapshot_id=context.market.snapshot_id,
    )


class DcaStrategy:
    strategy_type = "dca"

    def __init__(self, config: dict[str, Any]):
        self.budget = _decimal(config, "budget_usdt")

    def validate_config(self) -> None:
        return None

    def evaluate(self, context: StrategyContext) -> list[Signal]:
        if context.market.price <= 0:
            return [_signal(context, context.market.instrument, "skip", Decimal("0"), "invalid_market_price", "invalid-price")]
        return [_signal(context, context.market.instrument, "buy", self.budget, "scheduled_dca", "scheduled")]


class MovingAverageStrategy:
    strategy_type = "moving_average"

    def __init__(self, config: dict[str, Any]):
        self.short_window = self._window(config, "short_window")
        self.long_window = self._window(config, "long_window")
        self.budget = _decimal(config, "budget_usdt")
        if self.short_window >= self.long_window:
            raise ValueError("short_window must be less than long_window")

    @staticmethod
    def _window(config: dict[str, Any], key: str) -> int:
        try:
            value = int(config[key])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"{key} must be an integer") from error
        if value < 2 or value > 200:
            raise ValueError(f"{key} must be between 2 and 200")
        return value

    def validate_config(self) -> None:
        return None

    def evaluate(self, context: StrategyContext) -> list[Signal]:
        if len(context.prices) < self.long_window:
            return [_signal(context, context.market.instrument, "skip", Decimal("0"), "insufficient_price_history", "insufficient-history")]
        short = sum(context.prices[-self.short_window:], Decimal("0")) / self.short_window
        long = sum(context.prices[-self.long_window:], Decimal("0")) / self.long_window
        previous = context.prices[:-1]
        if len(previous) < self.long_window:
            return [_signal(context, context.market.instrument, "skip", Decimal("0"), "insufficient_previous_history", "insufficient-previous")]
        previous_short = sum(previous[-self.short_window:], Decimal("0")) / self.short_window
        previous_long = sum(previous[-self.long_window:], Decimal("0")) / self.long_window
        if previous_short <= previous_long and short > long:
            return [_signal(context, context.market.instrument, "buy", self.budget, "moving_average_golden_cross", "golden-cross")]
        if previous_short >= previous_long and short < long:
            return [_signal(context, context.market.instrument, "sell", self.budget, "moving_average_death_cross", "death-cross")]
        return [_signal(context, context.market.instrument, "hold", Decimal("0"), "moving_average_no_cross", "no-cross")]


class GridStrategy:
    strategy_type = "grid"

    def __init__(self, config: dict[str, Any]):
        self.lower = _decimal(config, "lower_price")
        self.upper = _decimal(config, "upper_price")
        self.grid_count = self._grid_count(config)
        self.budget = _decimal(config, "budget_usdt")
        if self.lower >= self.upper:
            raise ValueError("lower_price must be less than upper_price")

    @staticmethod
    def _grid_count(config: dict[str, Any]) -> int:
        try:
            value = int(config["grid_count"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("grid_count must be an integer") from error
        if value < 2 or value > 200:
            raise ValueError("grid_count must be between 2 and 200")
        return value

    def validate_config(self) -> None:
        return None

    def evaluate(self, context: StrategyContext) -> list[Signal]:
        price = context.market.price
        if price < self.lower or price > self.upper:
            return [_signal(context, context.market.instrument, "skip", Decimal("0"), "grid_out_of_range", "out-of-range")]
        if context.previous_price is None:
            return [_signal(context, context.market.instrument, "hold", Decimal("0"), "grid_initialized", "initialized")]
        step = (self.upper - self.lower) / self.grid_count
        crossed = [self.lower + step * index for index in range(1, self.grid_count)]
        if context.previous_price > price:
            levels = [level for level in crossed if price <= level < context.previous_price]
            if levels:
                return [_signal(context, context.market.instrument, "buy", self.budget, "grid_lower_level_reached", f"buy-{len(levels)}")]
        if context.previous_price < price:
            levels = [level for level in crossed if context.previous_price < level <= price]
            if levels:
                return [_signal(context, context.market.instrument, "sell", self.budget, "grid_upper_level_reached", f"sell-{len(levels)}")]
        return [_signal(context, context.market.instrument, "hold", Decimal("0"), "grid_no_level_crossed", "no-cross")]


class PortfolioDcaStrategy:
    strategy_type = "portfolio_dca"

    def __init__(self, config: dict[str, Any]):
        self.budget = _decimal(config, "budget_usdt")
        raw_allocations = config.get("allocations")
        if not isinstance(raw_allocations, list) or not raw_allocations:
            raise ValueError("allocations must be a non-empty list")
        self.allocations = tuple(self._allocation(item) for item in raw_allocations)
        total = sum(item["weight"] for item in self.allocations)
        if total != Decimal("1"):
            raise ValueError("allocation weights must total 1")

    @staticmethod
    def _allocation(item: Any) -> dict[str, Any]:
        if not isinstance(item, dict) or not item.get("instrument"):
            raise ValueError("each allocation needs an instrument")
        weight = _decimal(item, "weight")
        return {"instrument": str(item["instrument"]), "weight": weight}

    def validate_config(self) -> None:
        return None

    def evaluate(self, context: StrategyContext) -> list[Signal]:
        signals: list[Signal] = []
        prices = {context.market.instrument: context.market.price}
        prices.update({str(item["instrument"]): Decimal(str(item["price"])) for item in context.allocations if item.get("price") is not None})
        for item in self.allocations:
            instrument = item["instrument"]
            if instrument not in prices or prices[instrument] <= 0:
                signals.append(_signal(context, instrument, "skip", Decimal("0"), "portfolio_market_unavailable", "market-unavailable"))
                continue
            notional = (self.budget * item["weight"]).quantize(Decimal("0.00000001"))
            signals.append(_signal(context, instrument, "buy", notional, "portfolio_rebalance_dca", "scheduled"))
        return signals


def build_strategy(strategy_type: str, config: dict[str, Any]) -> Strategy:
    strategies = {"dca": DcaStrategy, "moving_average": MovingAverageStrategy, "grid": GridStrategy, "portfolio_dca": PortfolioDcaStrategy}
    try:
        return strategies[strategy_type](config)
    except KeyError as error:
        raise ValueError(f"unsupported strategy type: {strategy_type}") from error
