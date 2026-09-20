"""Persistence boundary for the AstraTrade MVP.

SQLite is deliberately used only as the first development adapter. The
application talks to this repository boundary, so production can replace the
adapter with PostgreSQL without moving business rules into SQL or HTTP code.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from .domain import (
    ConnectionStatus,
    OAuthConnection,
    OrderIntent,
    OrderRecord,
    OrderState,
    RiskDecision,
    Signal,
    Strategy,
    User,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class Repository:
    """Small transactional repository for users, strategies, and orders."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.create_schema()

    @classmethod
    def in_memory(cls) -> "Repository":
        return cls(sqlite3.connect(":memory:"))

    def close(self) -> None:
        self.connection.close()

    def create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                risk_confirmed INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS strategies (
                strategy_id TEXT NOT NULL,
                version TEXT NOT NULL,
                status TEXT NOT NULL,
                allowed_instruments TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (strategy_id, version)
            );
            CREATE TABLE IF NOT EXISTS signals (
                signal_id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                instrument TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price TEXT NOT NULL,
                stop_loss TEXT NOT NULL,
                quantity TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS risk_decisions (
                signal_id TEXT PRIMARY KEY REFERENCES signals(signal_id),
                allowed INTEGER NOT NULL,
                reasons TEXT NOT NULL,
                estimated_risk TEXT NOT NULL,
                estimated_notional TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders (
                client_order_id TEXT PRIMARY KEY,
                signal_id TEXT NOT NULL REFERENCES signals(signal_id),
                user_id TEXT NOT NULL REFERENCES users(user_id),
                instrument TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity TEXT NOT NULL,
                price TEXT NOT NULL,
                builder_code TEXT,
                state TEXT NOT NULL,
                exchange_order_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS order_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_order_id TEXT NOT NULL REFERENCES orders(client_order_id),
                state TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                event_type TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_states (
                state TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(user_id),
                redirect_uri TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS exchange_connections (
                connection_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(user_id),
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                encrypted_access_token TEXT NOT NULL,
                encrypted_refresh_token TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                scopes TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(user_id),
                expires_at TEXT NOT NULL,
                revoked INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def save_user(self, user: User) -> None:
        self.connection.execute(
            """
            INSERT INTO users(user_id, email, status, risk_confirmed, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              email=excluded.email,
              status=excluded.status,
              risk_confirmed=excluded.risk_confirmed
            """,
            (user.user_id, user.email, user.status, int(user.risk_confirmed), _now()),
        )
        self.connection.commit()

    def get_user(self, user_id: str) -> Optional[User]:
        row = self.connection.execute(
            "SELECT user_id, email, status, risk_confirmed FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return User(row["user_id"], row["email"], row["status"], bool(row["risk_confirmed"]))

    def save_strategy(self, strategy: Strategy) -> None:
        self.connection.execute(
            """
            INSERT INTO strategies(strategy_id, version, status, allowed_instruments, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(strategy_id, version) DO UPDATE SET
              status=excluded.status,
              allowed_instruments=excluded.allowed_instruments
            """,
            (
                strategy.strategy_id,
                strategy.version,
                strategy.status,
                json.dumps(sorted(strategy.allowed_instruments)),
                _now(),
            ),
        )
        self.connection.commit()

    def save_signal(self, signal: Signal) -> None:
        self.connection.execute(
            """
            INSERT INTO signals(
              signal_id, strategy_id, strategy_version, instrument, side,
              entry_price, stop_loss, quantity, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_id) DO NOTHING
            """,
            (
                signal.signal_id,
                signal.strategy_id,
                signal.strategy_version,
                signal.instrument,
                signal.side,
                str(signal.entry_price),
                str(signal.stop_loss),
                str(signal.quantity),
                signal.created_at.isoformat(),
                signal.expires_at.isoformat(),
            ),
        )
        self.connection.commit()

    def save_risk_decision(self, signal_id: str, decision: RiskDecision) -> None:
        self.connection.execute(
            """
            INSERT INTO risk_decisions(
              signal_id, allowed, reasons, estimated_risk, estimated_notional, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_id) DO UPDATE SET
              allowed=excluded.allowed,
              reasons=excluded.reasons,
              estimated_risk=excluded.estimated_risk,
              estimated_notional=excluded.estimated_notional,
              created_at=excluded.created_at
            """,
            (
                signal_id,
                int(decision.allowed),
                json.dumps(list(decision.reasons)),
                str(decision.estimated_risk),
                str(decision.estimated_notional),
                _now(),
            ),
        )
        self.connection.commit()

    def create_order(self, intent: OrderIntent, decision: RiskDecision) -> OrderRecord:
        """Create one order intent, or return the existing idempotent record."""
        if not decision.allowed:
            raise ValueError("cannot create order from denied risk decision")
        existing = self.get_order(intent.client_order_id)
        if existing is not None:
            if existing.intent != intent:
                raise ValueError("client order id already belongs to another intent")
            return existing
        if self.get_user(intent.user_id) is None:
            raise ValueError("unknown user")
        if self.connection.execute(
            "SELECT signal_id FROM signals WHERE signal_id = ?", (intent.signal_id,)
        ).fetchone() is None:
            raise ValueError("unknown signal")

        now = _now()
        self.connection.execute(
            """
            INSERT INTO orders(
              client_order_id, signal_id, user_id, instrument, side, quantity,
              price, builder_code, state, exchange_order_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                intent.client_order_id,
                intent.signal_id,
                intent.user_id,
                intent.instrument,
                intent.side,
                str(intent.quantity),
                str(intent.price),
                intent.builder_code,
                OrderState.INTENT.value,
                now,
                now,
            ),
        )
        self.connection.execute(
            "INSERT INTO order_events(client_order_id, state, occurred_at) VALUES (?, ?, ?)",
            (intent.client_order_id, OrderState.INTENT.value, now),
        )
        self.connection.commit()
        return self.get_order(intent.client_order_id)  # type: ignore[return-value]

    def advance_order(
        self,
        client_order_id: str,
        next_state: OrderState,
        exchange_order_id: Optional[str] = None,
    ) -> OrderRecord:
        order = self.get_order(client_order_id)
        if order is None:
            raise ValueError("unknown order")
        order.advance(next_state, exchange_order_id)
        now = _now()
        self.connection.execute(
            """
            UPDATE orders SET state = ?, exchange_order_id = COALESCE(?, exchange_order_id), updated_at = ?
            WHERE client_order_id = ?
            """,
            (next_state.value, exchange_order_id, now, client_order_id),
        )
        self.connection.execute(
            "INSERT INTO order_events(client_order_id, state, occurred_at) VALUES (?, ?, ?)",
            (client_order_id, next_state.value, now),
        )
        self.connection.commit()
        return self.get_order(client_order_id)  # type: ignore[return-value]

    def get_order(self, client_order_id: str) -> Optional[OrderRecord]:
        row = self.connection.execute(
            "SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,)
        ).fetchone()
        if row is None:
            return None
        intent = OrderIntent(
            client_order_id=row["client_order_id"],
            signal_id=row["signal_id"],
            user_id=row["user_id"],
            instrument=row["instrument"],
            side=row["side"],
            quantity=Decimal(row["quantity"]),
            price=Decimal(row["price"]),
            builder_code=row["builder_code"],
        )
        history = [
            OrderState(event["state"])
            for event in self.connection.execute(
                "SELECT state FROM order_events WHERE client_order_id = ? ORDER BY id",
                (client_order_id,),
            ).fetchall()
        ]
        return OrderRecord(
            intent=intent,
            state=OrderState(row["state"]),
            exchange_order_id=row["exchange_order_id"],
            history=history,
        )

    def audit(self, event_type: str, subject_id: str, payload: dict, user_id: Optional[str] = None) -> None:
        self.connection.execute(
            """
            INSERT INTO audit_events(user_id, event_type, subject_id, payload, occurred_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, event_type, subject_id, json.dumps(payload, sort_keys=True), _now()),
        )
        self.connection.commit()

    def audit_count(self, subject_id: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM audit_events WHERE subject_id = ?", (subject_id,)
        ).fetchone()
        return int(row["count"])

    def save_oauth_state(
        self, state: str, user_id: str, redirect_uri: str, expires_at: datetime
    ) -> None:
        if self.get_user(user_id) is None:
            raise ValueError("unknown user")
        self.connection.execute(
            """
            INSERT INTO oauth_states(state, user_id, redirect_uri, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (state, user_id, redirect_uri, expires_at.isoformat(), _now()),
        )
        self.connection.commit()

    def consume_oauth_state(
        self, state: str, now: Optional[datetime] = None
    ) -> Optional[tuple[str, str]]:
        """Return the pending user/redirect exactly once, if still valid."""
        row = self.connection.execute(
            "SELECT user_id, redirect_uri, expires_at FROM oauth_states WHERE state = ?",
            (state,),
        ).fetchone()
        self.connection.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
        self.connection.commit()
        if row is None:
            return None
        current = now or datetime.now(timezone.utc)
        if current > _dt(row["expires_at"]):
            return None
        return row["user_id"], row["redirect_uri"]

    def save_exchange_connection(self, connection: OAuthConnection) -> None:
        now = _now()
        self.connection.execute(
            """
            INSERT INTO exchange_connections(
              connection_id, user_id, provider, status, encrypted_access_token,
              encrypted_refresh_token, expires_at, scopes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(connection_id) DO UPDATE SET
              user_id=excluded.user_id,
              provider=excluded.provider,
              status=excluded.status,
              encrypted_access_token=excluded.encrypted_access_token,
              encrypted_refresh_token=excluded.encrypted_refresh_token,
              expires_at=excluded.expires_at,
              scopes=excluded.scopes,
              updated_at=excluded.updated_at
            """,
            (
                connection.connection_id,
                connection.user_id,
                connection.provider,
                connection.status.value,
                connection.encrypted_access_token,
                connection.encrypted_refresh_token,
                connection.expires_at.isoformat(),
                json.dumps(sorted(connection.scopes)),
                now,
                now,
            ),
        )
        self.connection.commit()

    def get_exchange_connection(self, user_id: str) -> Optional[OAuthConnection]:
        row = self.connection.execute(
            """
            SELECT * FROM exchange_connections
            WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return OAuthConnection(
            connection_id=row["connection_id"],
            user_id=row["user_id"],
            provider=row["provider"],
            status=ConnectionStatus(row["status"]),
            encrypted_access_token=row["encrypted_access_token"],
            encrypted_refresh_token=row["encrypted_refresh_token"],
            expires_at=_dt(row["expires_at"]),
            scopes=frozenset(json.loads(row["scopes"])),
        )

    def set_connection_status(self, user_id: str, status: ConnectionStatus) -> None:
        self.connection.execute(
            "UPDATE exchange_connections SET status = ?, updated_at = ? WHERE user_id = ?",
            (status.value, _now(), user_id),
        )
        self.connection.commit()

    def get_usable_exchange_connection(
        self, user_id: str, now: Optional[datetime] = None
    ) -> Optional[OAuthConnection]:
        connection = self.get_exchange_connection(user_id)
        if connection is None or connection.status != ConnectionStatus.ACTIVE:
            return None
        current = now or datetime.now(timezone.utc)
        if current >= connection.expires_at:
            self.set_connection_status(user_id, ConnectionStatus.EXPIRED)
            return None
        return connection

    def create_session(self, token_hash: str, user_id: str, expires_at: datetime) -> None:
        if self.get_user(user_id) is None:
            raise ValueError("unknown user")
        self.connection.execute(
            """
            INSERT INTO sessions(token_hash, user_id, expires_at, revoked, created_at)
            VALUES (?, ?, ?, 0, ?)
            """,
            (token_hash, user_id, expires_at.isoformat(), _now()),
        )
        self.connection.commit()

    def get_active_session_user(
        self, token_hash: str, now: Optional[datetime] = None
    ) -> Optional[str]:
        row = self.connection.execute(
            """
            SELECT sessions.user_id, sessions.expires_at, users.status
            FROM sessions JOIN users ON users.user_id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.revoked = 0
            """,
            (token_hash,),
        ).fetchone()
        if row is None or row["status"] != "active":
            return None
        current = now or datetime.now(timezone.utc)
        if current >= _dt(row["expires_at"]):
            return None
        return row["user_id"]

    def revoke_session(self, token_hash: str) -> None:
        self.connection.execute(
            "UPDATE sessions SET revoked = 1 WHERE token_hash = ?", (token_hash,)
        )
        self.connection.commit()
