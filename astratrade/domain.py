"""Stable domain objects shared by strategy, risk, and execution layers.

These objects intentionally do not know about HTTP, databases, OAuth, or an
exchange SDK. Keeping the first P0 boundary pure makes the safety rules
testable before any external side effect is introduced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import FrozenSet, Mapping, Optional


class AgentStatus(str, Enum):
    DRAFT = "draft"
    SIMULATING = "simulating"
    READY_FOR_LIVE = "ready_for_live"
    RUNNING = "running"
    PAUSED = "paused"
    REVOKED = "revoked"
    SUSPENDED = "suspended"
    FAILED = "failed"


class OrderState(str, Enum):
    INTENT = "intent"
    RISK_APPROVED = "risk_approved"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"
    RECONCILED = "reconciled"


@dataclass(frozen=True)
class RiskProfile:
    """User-level limits. Monetary values are expressed in quote currency."""

    max_order_risk_pct: Decimal
    max_position_notional: Decimal
    max_daily_loss: Decimal
    max_leverage: Decimal
    allowed_instruments: FrozenSet[str]
    max_orders_per_day: int


@dataclass(frozen=True)
class AccountSnapshot:
    """The minimum account facts required before adding new risk."""

    equity: Decimal
    realized_pnl_today: Decimal
    position_notional: Decimal
    active_orders: int
    leverage: Decimal
    observed_at: datetime
    authorization_valid: bool


@dataclass(frozen=True)
class Signal:
    """A strategy output; it is not an order and cannot submit one."""

    signal_id: str
    strategy_id: str
    strategy_version: str
    instrument: str
    side: str
    entry_price: Decimal
    stop_loss: Decimal
    quantity: Decimal
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reasons: tuple[str, ...] = ()
    estimated_risk: Decimal = Decimal("0")
    estimated_notional: Decimal = Decimal("0")


@dataclass(frozen=True)
class OrderIntent:
    """A deterministic request waiting for execution after risk approval."""

    client_order_id: str
    signal_id: str
    user_id: str
    instrument: str
    side: str
    quantity: Decimal
    price: Decimal
    builder_code: Optional[str] = None


_TERMINAL_STATES = frozenset(
    {
        OrderState.REJECTED,
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.FAILED,
        OrderState.RECONCILED,
    }
)

_ALLOWED_TRANSITIONS: Mapping[OrderState, FrozenSet[OrderState]] = {
    OrderState.INTENT: frozenset({OrderState.RISK_APPROVED, OrderState.REJECTED}),
    OrderState.RISK_APPROVED: frozenset({OrderState.SUBMITTED, OrderState.FAILED}),
    OrderState.SUBMITTED: frozenset(
        {OrderState.ACCEPTED, OrderState.REJECTED, OrderState.FAILED}
    ),
    OrderState.ACCEPTED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.FAILED,
        }
    ),
    OrderState.PARTIALLY_FILLED: frozenset(
        {OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.CANCELLED, OrderState.FAILED}
    ),
    OrderState.RECONCILED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.FILLED: frozenset({OrderState.RECONCILED}),
    OrderState.CANCELLED: frozenset({OrderState.RECONCILED}),
    OrderState.FAILED: frozenset({OrderState.RECONCILED}),
}


@dataclass
class OrderRecord:
    """Auditable local view of an order lifecycle."""

    intent: OrderIntent
    state: OrderState = OrderState.INTENT
    exchange_order_id: Optional[str] = None
    history: list[OrderState] = field(default_factory=lambda: [OrderState.INTENT])

    def advance(self, next_state: OrderState, exchange_order_id: Optional[str] = None) -> None:
        if self.state in _TERMINAL_STATES and self.state != OrderState.FILLED:
            raise ValueError("cannot advance terminal order %s" % self.state.value)
        if next_state not in _ALLOWED_TRANSITIONS[self.state]:
            raise ValueError(
                "invalid order transition: %s -> %s"
                % (self.state.value, next_state.value)
            )
        self.state = next_state
        self.history.append(next_state)
        if exchange_order_id:
            self.exchange_order_id = exchange_order_id
