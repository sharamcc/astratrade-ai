"""Deterministic pre-trade risk decisions."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import AbstractSet

from .domain import AccountSnapshot, AgentStatus, RiskDecision, RiskProfile, Signal


STALE_DATA_SECONDS = 30


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def evaluate_signal(
    signal: Signal,
    profile: RiskProfile,
    account: AccountSnapshot,
    *,
    agent_status: AgentStatus,
    strategy_published: bool,
    now: datetime,
    seen_signal_ids: AbstractSet[str] = frozenset(),
    platform_enabled: bool = True,
) -> RiskDecision:
    """Return an allow/deny decision without performing external side effects.

    A deny-by-default result is returned whenever required account or market
    facts are stale or unknown. The caller must persist this decision before
    handing an approved intent to an execution adapter.
    """

    reasons: list[str] = []
    estimated_risk = abs(signal.entry_price - signal.stop_loss) * signal.quantity
    estimated_notional = abs(signal.entry_price * signal.quantity)

    if not platform_enabled:
        reasons.append("PLATFORM_SUSPENDED")
    if agent_status != AgentStatus.RUNNING:
        reasons.append("AGENT_NOT_RUNNING")
    if not strategy_published:
        reasons.append("STRATEGY_NOT_PUBLISHED")
    if not account.authorization_valid:
        reasons.append("AUTHORIZATION_INVALID")
    if signal.instrument not in profile.allowed_instruments:
        reasons.append("INSTRUMENT_NOT_ALLOWED")
    if signal.signal_id in seen_signal_ids:
        reasons.append("DUPLICATE_SIGNAL")

    current = _as_utc(now)
    if current > _as_utc(signal.expires_at):
        reasons.append("SIGNAL_EXPIRED")
    age = (current - _as_utc(account.observed_at)).total_seconds()
    if age < 0 or age > STALE_DATA_SECONDS:
        reasons.append("ACCOUNT_DATA_STALE")

    if signal.side not in {"long", "short"}:
        reasons.append("INVALID_SIDE")
    if signal.quantity <= 0 or signal.entry_price <= 0:
        reasons.append("INVALID_ORDER_SIZE")
    if signal.stop_loss <= 0:
        reasons.append("INVALID_STOP_LOSS")
    if signal.side == "long" and signal.stop_loss >= signal.entry_price:
        reasons.append("LONG_STOP_MUST_BE_BELOW_ENTRY")
    if signal.side == "short" and signal.stop_loss <= signal.entry_price:
        reasons.append("SHORT_STOP_MUST_BE_ABOVE_ENTRY")

    if account.equity <= 0:
        reasons.append("INVALID_ACCOUNT_EQUITY")
    else:
        max_order_risk = account.equity * profile.max_order_risk_pct / Decimal("100")
        if estimated_risk > max_order_risk:
            reasons.append("ORDER_RISK_LIMIT_EXCEEDED")

    if account.position_notional + estimated_notional > profile.max_position_notional:
        reasons.append("POSITION_NOTIONAL_LIMIT_EXCEEDED")
    if account.leverage > profile.max_leverage:
        reasons.append("LEVERAGE_LIMIT_EXCEEDED")
    if account.realized_pnl_today <= -abs(profile.max_daily_loss):
        reasons.append("DAILY_LOSS_LIMIT_REACHED")
    if account.active_orders >= profile.max_orders_per_day:
        reasons.append("ORDER_COUNT_LIMIT_REACHED")

    return RiskDecision(
        allowed=not reasons,
        reasons=tuple(reasons),
        estimated_risk=estimated_risk,
        estimated_notional=estimated_notional,
    )
